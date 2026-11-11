# Run only in a disposable Windows user profile (CI). This uses the real default
# LocalAppData index location and refuses to touch an existing installation/data.
[CmdletBinding()]
param(
    [Alias('BaselineInstaller')][Parameter(Mandatory = $true)][string]$PreviousInstaller,
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$Python = (Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe'),
    [string]$ExpectedVersion = '0.2.1',
    [string]$BaselineVersion = '0.2.0',
    [string]$WorkRoot = (Join-Path $PSScriptRoot '..\..\build\installer-tests')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Quote([string]$Value) { return '"' + $Value + '"' }
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit(180000)) {
        Stop-Process -Id $process.Id -Force
        throw "Command timed out: $File"
    }
    $process.Refresh()
    Require ($process.ExitCode -eq 0) "Command failed ($($process.ExitCode)): $File"
}
function Check-ChineseWizard([string]$File) {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $started = Start-Process -FilePath $File -ArgumentList @('/SP-', '/NORESTART') -PassThru
    $wizardProcess = $null
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        $wizard = $null
        $next = $null
        do {
            Start-Sleep -Milliseconds 250
            $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($candidate in $windows) {
                if ($candidate.Current.Name -notmatch '图迹') { continue }
                $buttons = $candidate.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    (New-Object System.Windows.Automation.PropertyCondition(
                        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                        [System.Windows.Automation.ControlType]::Button)))
                foreach ($button in $buttons) {
                    if ($button.Current.Name -match '下一步') { $wizard = $candidate; $next = $button; break }
                }
                if ($null -ne $wizard) { break }
            }
        } while ($null -eq $wizard -and [DateTime]::UtcNow -lt $deadline)
        Require ($null -ne $wizard -and $null -ne $next) 'Chinese installation wizard/下一步 button could not be verified on the native Windows desktop'
        $wizardProcess = Get-Process -Id $wizard.Current.ProcessId
        $evidence = [ordered]@{ title = $wizard.Current.Name; next_button = $next.Current.Name }
        $buttons = $wizard.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Button)))
        $cancel = $null
        foreach ($button in $buttons) { if ($button.Current.Name -match '取消') { $cancel = $button; break } }
        Require ($null -ne $cancel) 'Chinese wizard has no cancel button'
        $cancel.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while (-not $wizardProcess.HasExited -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
            $all = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                (New-Object System.Windows.Automation.PropertyCondition(
                    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $wizardProcess.Id)))
            foreach ($element in $all) {
                if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                    $element.Current.Name -match '^是') {
                    $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                    break
                }
            }
            $wizardProcess.Refresh()
        }
        Require ($wizardProcess.HasExited) 'Cancelled installer did not close'
        return $evidence
    } finally {
        if ($null -ne $wizardProcess -and -not $wizardProcess.HasExited) { Stop-Process -Id $wizardProcess.Id -Force }
        if (-not $started.HasExited) { Stop-Process -Id $started.Id -Force }
    }
}
function Snapshot([string]$Directory) {
    $items = @(Get-ChildItem -LiteralPath $Directory -File -Recurse | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{ path = $_.FullName.Substring($Directory.Length); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
    Require ($items.Count -gt 7) 'Expected an actual index, thumbnails and sources before preservation check'
    return (ConvertTo-Json -InputObject $items -Compress)
}
function Check-Install([string]$Version) {
    Require (Test-Path -LiteralPath $script:Executable) 'Installed executable is missing'
    $entry = Get-ItemProperty -LiteralPath $script:RegistryKey
    Require ($entry.DisplayName -match '图迹') 'Uninstall entry must have a Chinese application name'
    Require ($entry.DisplayVersion -eq $Version) "Wrong installed version: $($entry.DisplayVersion)"
    Require ($entry.UninstallString -match 'unins[0-9]+\.exe') 'System uninstall command is missing'
    Require ((Split-Path $entry.UninstallString.Trim('"') -Parent) -eq $script:InstallRoot) 'Wrong uninstall directory'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($link in $script:Links) {
        Require (Test-Path -LiteralPath $link) "Shortcut is missing: $link"
        $shortcut = $shell.CreateShortcut($link)
        if ($link -notmatch '卸载') {
            Require ($shortcut.TargetPath -eq $script:Executable) "Shortcut target is wrong: $link"
        }
    }
}
function Install([string]$File, [string]$Name) {
    Invoke-Checked $File @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        '/TASKS=desktopicon', ('/DIR=' + (Quote $script:InstallRoot)),
        ('/LOG=' + (Quote (Join-Path $script:WorkRoot ($Name + '.log')))))
}
function Uninstall([string]$Name) {
    $uninstaller = @(Get-ChildItem -LiteralPath $script:InstallRoot -Filter 'unins*.exe')
    Require ($uninstaller.Count -eq 1) 'Expected exactly one registered uninstaller'
    Invoke-Checked $uninstaller[0].FullName @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        ('/LOG=' + (Quote (Join-Path $script:WorkRoot ($Name + '.log')))))
    Require (-not (Test-Path -LiteralPath $script:Executable)) 'Uninstall left the application executable'
    Require (-not (Test-Path -LiteralPath $script:RegistryKey)) 'Uninstall left its system registry entry'
    foreach ($link in $script:Links) { Require (-not (Test-Path -LiteralPath $link)) "Uninstall left shortcut: $link" }
}
function Open-NormalWindow {
    $process = Start-Process -FilePath $script:Executable -PassThru
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        do {
            Start-Sleep -Milliseconds 200
            $process.Refresh()
        } while (-not $process.HasExited -and $process.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)
        Require (-not $process.HasExited) 'Installed application exited before opening its window'
        Require ($process.MainWindowHandle -ne 0) 'Installed application did not open a native Windows window'
        Require ($process.CloseMainWindow()) 'Unable to close the installed application normally'
        Require ($process.WaitForExit(30000)) 'Installed application did not shut down after window close'
        Require ($process.ExitCode -eq 0) 'Installed application failed on normal startup/shutdown'
    } finally {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}
function Packaged-Smoke([string]$Name) {
    $report = Join-Path $script:WorkRoot ($Name + '.json')
    # Remove development Python from the executable's lookup path: all runtime
    # imports, native DLLs and Qt plugins must come from the installed package.
    $previousPath = $env:PATH
    $previousPythonHome = $env:PYTHONHOME
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        $env:PYTHONHOME = $null
        $env:PYTHONPATH = $null
        Invoke-Checked $script:Executable @('--self-test-report', (Quote $report), '--data-dir', (Quote $script:DataRoot))
    } finally {
        $env:PATH = $previousPath
        $env:PYTHONHOME = $previousPythonHome
        $env:PYTHONPATH = $previousPythonPath
    }
    $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    Require ($result.status -eq 'passed') "Packaged regression failed: $($result | ConvertTo-Json -Depth 5)"
    Require ($result.frozen -eq $true) 'Regression did not execute inside the frozen app'
    Require ($result.version -eq $script:ExpectedVersion) 'Installed executable has the wrong application version'
    Require ($result.index_existed -and -not $result.first_run -and $result.scan_unchanged -eq 5) 'Retained index was missing or rebuilt'
    Require ($result.whole_matches -eq 5 -and $result.partial_matches -eq 5 -and $result.qt_window) 'Incomplete packaged functional regression'
    return $result
}

Require ($env:OS -eq 'Windows_NT') 'This acceptance test requires native Windows'
$PreviousInstaller = (Resolve-Path -LiteralPath $PreviousInstaller).Path
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$WorkRoot = [IO.Path]::GetFullPath($WorkRoot)
$InstallRoot = Join-Path $WorkRoot 'installed 图迹'
$Executable = Join-Path $InstallRoot 'ImageTools.exe'
$DataRoot = Join-Path $env:LOCALAPPDATA 'ImageTools'
$RegistryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6BC68160-8131-4515-A3A3-B92332CDCF56}_is1'
$Links = @(
    (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) '图迹.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) '图迹\图迹.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) '图迹\卸载图迹.lnk')
)
Require (-not (Test-Path -LiteralPath $RegistryKey)) 'Use a disposable Windows user: an installation already exists'
Require (-not (Test-Path -LiteralPath $DataRoot)) 'Use a disposable Windows user: existing index/data must not be touched'
Require (-not (Test-Path -LiteralPath $WorkRoot)) 'WorkRoot already exists; use a fresh directory'
foreach ($link in $Links) { Require (-not (Test-Path -LiteralPath $link)) 'Use a disposable Windows user: a shortcut already exists' }
Require (-not $env:IMAGE_TOOLS_DATA_DIR) 'Remove IMAGE_TOOLS_DATA_DIR in the disposable test user to test the real default location'
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
$previousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = 'windows'
$outcome = [ordered]@{ status = 'failed'; baseline = $BaselineVersion; candidate = $ExpectedVersion; checks = @(); work_root = $WorkRoot }
try {
    $outcome.chinese_wizard = Check-ChineseWizard $Installer
    Require (-not (Test-Path -LiteralPath $RegistryKey)) 'Cancelling the wizard unexpectedly installed the application'
    $outcome.checks += 'native-chinese-wizard-next-cancel'
    # Create a real persistent index and thumbnails using repository code, then
    # open them with the actual old installed executable before upgrade.
    Invoke-Checked $Python @('-m', 'image_tools', '--self-test-report', (Quote (Join-Path $WorkRoot 'seed.json')),
        '--data-dir', (Quote $DataRoot))
    Install $PreviousInstaller 'install-baseline'
    Check-Install $BaselineVersion
    Open-NormalWindow
    $outcome.checks += 'baseline-install-and-native-start'
    $beforeUpgrade = Snapshot $DataRoot
    Install $Installer 'upgrade'
    Check-Install $ExpectedVersion
    Require ((Snapshot $DataRoot) -eq $beforeUpgrade) 'Upgrade changed or removed the existing index/data'
    $outcome.checks += 'upgrade-preserves-index-and-thumbnails-byte-for-byte'
    $outcome.upgraded_app = Packaged-Smoke 'upgraded-app'
    Open-NormalWindow
    $outcome.checks += 'upgraded-exe-runtime-qt-scan-whole-partial-persistence'
    $beforeUninstall = Snapshot $DataRoot
    Uninstall 'uninstall'
    Require ((Snapshot $DataRoot) -eq $beforeUninstall) 'Uninstall changed or removed the existing index/data'
    $outcome.checks += 'uninstall-removes-app-shortcuts-registry-and-retains-data'
    Install $Installer 'reinstall'
    Check-Install $ExpectedVersion
    Require ((Snapshot $DataRoot) -eq $beforeUninstall) 'Reinstall changed existing index/data'
    $outcome.reinstalled_app = Packaged-Smoke 'reinstalled-app'
    $outcome.checks += 'reinstall-can-search-retained-index'
    $beforeFinalUninstall = Snapshot $DataRoot
    Uninstall 'final-uninstall'
    Require ((Snapshot $DataRoot) -eq $beforeFinalUninstall) 'Final uninstall changed existing index/data'
    $outcome.status = 'passed'
} catch {
    $outcome.error = $_.ToString()
    throw
} finally {
    $env:QT_QPA_PLATFORM = $previousQtPlatform
    $outcome | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $WorkRoot 'installer-regression.json') -Encoding UTF8
}
Write-Host "Windows installer lifecycle and packaged regressions passed. Evidence: $WorkRoot"
