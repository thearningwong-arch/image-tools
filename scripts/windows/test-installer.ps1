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
    # Inno Setup's native TNewButton controls are not consistently exposed by
    # UIAutomation on hosted runners. Inspect and invoke the actual Win32
    # controls instead; the Chinese labels remain mandatory acceptance checks.
    if ($null -eq ('ImageToolsInstallerTests.NativeWindows' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
namespace ImageToolsInstallerTests {
    public sealed class NativeWindow {
        public IntPtr Handle;
        public string Title;
        public string ClassName;
        public uint ProcessId;
        public string ProcessName;
        public int ControlId;
    }
    public static class NativeWindows {
        private delegate bool EnumCallback(IntPtr window, IntPtr parameter);
        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumCallback callback, IntPtr parameter);
        [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, EnumCallback callback, IntPtr parameter);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowTextW(IntPtr window, StringBuilder text, int maximum);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassNameW(IntPtr window, StringBuilder text, int maximum);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
        [DllImport("user32.dll")] private static extern int GetDlgCtrlID(IntPtr window);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr SendMessageTimeoutW(IntPtr window, uint message, UIntPtr wParam,
            StringBuilder text, uint flags, uint timeout, out UIntPtr result);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr SendMessageTimeoutW(IntPtr window, uint message, UIntPtr wParam,
            IntPtr lParam, uint flags, uint timeout, out UIntPtr result);
        private static NativeWindow Read(IntPtr window) {
            var text = new StringBuilder(2048);
            GetWindowTextW(window, text, text.Capacity);
            if (text.Length == 0) {
                UIntPtr result;
                // WM_GETTEXT retrieves captions from controls in another
                // process, including Delphi/Inno Setup's native controls.
                SendMessageTimeoutW(window, 0x000D, (UIntPtr)(uint)text.Capacity, text, 0x0002, 300, out result);
            }
            var className = new StringBuilder(256);
            GetClassNameW(window, className, className.Capacity);
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            string processName;
            try { processName = Process.GetProcessById((int)processId).ProcessName; }
            catch { processName = "exited"; }
            return new NativeWindow { Handle = window, Title = text.ToString(),
                ClassName = className.ToString(), ProcessId = processId, ProcessName = processName,
                ControlId = GetDlgCtrlID(window) };
        }
        public static NativeWindow[] VisibleWindows() {
            var windows = new List<NativeWindow>();
            EnumWindows(delegate(IntPtr window, IntPtr parameter) {
                if (IsWindowVisible(window)) windows.Add(Read(window));
                return true;
            }, IntPtr.Zero);
            return windows.ToArray();
        }
        public static NativeWindow[] VisibleChildren(IntPtr parent) {
            var windows = new List<NativeWindow>();
            EnumChildWindows(parent, delegate(IntPtr window, IntPtr parameter) {
                if (IsWindowVisible(window)) windows.Add(Read(window));
                return true;
            }, IntPtr.Zero);
            return windows.ToArray();
        }
        public static bool Click(IntPtr button) {
            UIntPtr result;
            // BM_CLICK may synchronously open the modal exit confirmation.
            // Bound that call so the caller can then find and answer it.
            return SendMessageTimeoutW(button, 0x00F5, UIntPtr.Zero, IntPtr.Zero,
                0x0002, 2000, out result) != IntPtr.Zero;
        }
    }
}
'@
    }
    function Get-WizardDiagnostics {
        $details = @([ImageToolsInstallerTests.NativeWindows]::VisibleWindows() | Select-Object -First 40 | ForEach-Object {
            [ordered]@{
                title = $_.Title; class = $_.ClassName; process_id = $_.ProcessId; process_name = $_.ProcessName
                controls = @([ImageToolsInstallerTests.NativeWindows]::VisibleChildren($_.Handle) |
                    Where-Object { $_.Title -or $_.ClassName -match 'Button' } |
                    Select-Object -First 80 | ForEach-Object { [ordered]@{ title = $_.Title; class = $_.ClassName } })
            }
        })
        return (ConvertTo-Json -InputObject $details -Depth 6 -Compress)
    }
    $started = Start-Process -FilePath $File -ArgumentList @('/SP-', '/NORESTART') -PassThru
    $wizardProcess = $null
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        $wizard = $null
        $next = $null
        $cancel = $null
        do {
            Start-Sleep -Milliseconds 250
            foreach ($candidate in [ImageToolsInstallerTests.NativeWindows]::VisibleWindows()) {
                if ($candidate.Title -notmatch '图迹') { continue }
                $children = @([ImageToolsInstallerTests.NativeWindows]::VisibleChildren($candidate.Handle))
                $nextButtons = @($children | Where-Object { $_.ClassName -match 'Button' -and $_.Title -match '下一步' })
                $cancelButtons = @($children | Where-Object { $_.ClassName -match 'Button' -and $_.Title -match '取消' })
                if ($nextButtons.Count -gt 0 -and $cancelButtons.Count -gt 0) {
                    $wizard = $candidate
                    $next = $nextButtons[0]
                    $cancel = $cancelButtons[0]
                    break
                }
            }
        } while ($null -eq $wizard -and [DateTime]::UtcNow -lt $deadline)
        if ($null -eq $wizard) {
            throw ('Chinese installation wizard/下一步/取消 buttons could not be verified on the native Windows desktop. ' +
                'Launcher process=' + $started.Id + '; visible Win32 windows=' + (Get-WizardDiagnostics))
        }
        $wizardProcess = Get-Process -Id $wizard.ProcessId
        $evidence = [ordered]@{ method = 'Win32 EnumWindows/EnumChildWindows/GetWindowTextW/WM_GETTEXT/BM_CLICK';
            title = $wizard.Title; window_class = $wizard.ClassName; process_name = $wizard.ProcessName;
            next_button = $next.Title; cancel_button = $cancel.Title }
        $null = [ImageToolsInstallerTests.NativeWindows]::Click($cancel.Handle)
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        $confirmationSeen = $false
        while (-not $wizardProcess.HasExited -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
            foreach ($dialog in [ImageToolsInstallerTests.NativeWindows]::VisibleWindows()) {
                if ($dialog.ProcessId -ne $wizard.ProcessId -or $dialog.Title -notmatch '退出安装') { continue }
                foreach ($button in [ImageToolsInstallerTests.NativeWindows]::VisibleChildren($dialog.Handle)) {
                    # IDYES is 6. Native message-box buttons use the Windows
                    # language (for example &Yes on an English test machine).
                    if ($button.ClassName -match 'Button' -and $button.ControlId -eq 6) {
                        $confirmationSeen = $true
                        $evidence.exit_confirmation = $dialog.Title
                        $evidence.confirm_button = $button.Title
                        $null = [ImageToolsInstallerTests.NativeWindows]::Click($button.Handle)
                        break
                    }
                }
            }
            $wizardProcess.Refresh()
        }
        if (-not $wizardProcess.HasExited) {
            throw ('Cancelled installer did not close. confirmationSeen=' + $confirmationSeen +
                '; visible Win32 windows=' + (Get-WizardDiagnostics))
        }
        # On the welcome page Inno may cancel without an exit confirmation.
        $evidence.exit_confirmation_seen = $confirmationSeen
        return $evidence
    } finally {
        if ($null -ne $wizardProcess -and -not $wizardProcess.HasExited) { Stop-Process -Id $wizardProcess.Id -Force -ErrorAction SilentlyContinue }
        if (-not $started.HasExited) { Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue }
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
    if ($null -eq ('ImageToolsInstallerTests.InstallPaths' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;
namespace ImageToolsInstallerTests {
    public sealed class ShortcutData {
        public string TargetPath;
        public string WorkingDirectory;
    }
    public static class InstallPaths {
        // IShellLinkW: preserve the native vtable order through the two getters
        // used below. IPersistFile.Load takes a Unicode absolute link filename.
        [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellLinkW {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                int maximum, IntPtr findData, uint flags);
            void GetIDList(out IntPtr idList);
            void SetIDList(IntPtr idList);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder description, int maximum);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string description);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder directory, int maximum);
        }
        public static ShortcutData ReadShortcut(string path) {
            ShortcutData data = null;
            Exception failure = null;
            var thread = new Thread(delegate() {
                object instance = null;
                try {
                    instance = Activator.CreateInstance(Type.GetTypeFromCLSID(
                        new Guid("00021401-0000-0000-C000-000000000046"), true));
                    ((System.Runtime.InteropServices.ComTypes.IPersistFile)instance).Load(Path.GetFullPath(path), 0);
                    var link = (IShellLinkW)instance;
                    var target = new StringBuilder(32768);
                    var directory = new StringBuilder(32768);
                    link.GetPath(target, target.Capacity, IntPtr.Zero, 4); // SLGP_RAWPATH
                    link.GetWorkingDirectory(directory, directory.Capacity);
                    if (target.Length == 0) throw new InvalidDataException("IShellLinkW returned an empty target");
                    data = new ShortcutData { TargetPath = target.ToString(), WorkingDirectory = directory.ToString() };
                } catch (Exception error) {
                    failure = error;
                } finally {
                    if (instance != null && Marshal.IsComObject(instance)) Marshal.FinalReleaseComObject(instance);
                }
            });
            // Do not depend on pwsh's default MTA or WScript.Shell's automation
            // wrapper when loading Unicode links created by native Inno Setup.
            thread.SetApartmentState(ApartmentState.STA);
            thread.IsBackground = true;
            thread.Start();
            if (!thread.Join(15000)) throw new TimeoutException("Native shell-link loading timed out");
            if (failure != null) throw new InvalidDataException("Native shell-link loading failed", failure);
            return data;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetLongPathNameW(string shortPath, StringBuilder longPath, uint maximum);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flags, IntPtr templateFile);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, uint maximum, uint flags);
        public static string CanonicalExistingFile(string path) {
            string expanded = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
            var longPath = new StringBuilder(32768);
            uint count = GetLongPathNameW(expanded, longPath, (uint)longPath.Capacity);
            if (count == 0 || count >= longPath.Capacity)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot expand existing path: " + expanded);
            // Resolve an existing file handle as well as expanding DOS 8.3 names.
            // This compares the actual destination path, not another file with
            // coincidentally identical contents or an unresolved shortcut path.
            using (var file = CreateFileW(longPath.ToString(), 0, 7, IntPtr.Zero, 3, 0, IntPtr.Zero)) {
                if (file.IsInvalid)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot open existing path: " + longPath);
                var finalPath = new StringBuilder(32768);
                count = GetFinalPathNameByHandleW(file, finalPath, (uint)finalPath.Capacity, 0);
                if (count == 0 || count >= finalPath.Capacity)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot resolve existing path: " + longPath);
                string result = finalPath.ToString();
                if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                    return @"\\" + result.Substring(8);
                if (result.StartsWith(@"\\?\", StringComparison.Ordinal)) return result.Substring(4);
                return result;
            }
        }
    }
}
'@
    }
    foreach ($link in $script:Links) {
        Require (Test-Path -LiteralPath $link) "Shortcut is missing: $link"
        $linkBytes = [IO.File]::ReadAllBytes($link)
        $linkBase64 = [Convert]::ToBase64String($linkBytes)
        if ($linkBytes.Length -lt 76) {
            throw "Shortcut file is empty or truncated: $link; link_bytes=$($linkBytes.Length); link_base64=$linkBase64"
        }
        $shortcut = $null
        try {
            $shortcut = [ImageToolsInstallerTests.InstallPaths]::ReadShortcut($link)
        } catch {
            throw ("Cannot read native Unicode shell link: $link; link_bytes=$($linkBytes.Length); " +
                "link_base64=$linkBase64; cause=$($_.Exception.ToString())")
        }
        $actual = $shortcut.TargetPath
        $expected = if ($link -match '卸载') { $entry.UninstallString.Trim('"') } else { $script:Executable }
        $canonicalActual = $null
        $canonicalExpected = $null
        try {
            $canonicalActual = [ImageToolsInstallerTests.InstallPaths]::CanonicalExistingFile($actual)
            $canonicalExpected = [ImageToolsInstallerTests.InstallPaths]::CanonicalExistingFile($expected)
        } catch {
            throw ("Shortcut target cannot be resolved: $link; actual=[$actual]; expected=[$expected]; " +
                "expanded_actual=[$canonicalActual]; expanded_expected=[$canonicalExpected]; " +
                "WorkingDirectory=[$($shortcut.WorkingDirectory)]; link_bytes=$($linkBytes.Length); " +
                "link_base64=$linkBase64; cause=$($_.Exception.Message)")
        }
        Require ([string]::Equals($canonicalActual, $canonicalExpected, [StringComparison]::OrdinalIgnoreCase)) (
            "Shortcut target is wrong: $link; actual=[$actual]; expected=[$expected]; " +
            "expanded_actual=[$canonicalActual]; expanded_expected=[$canonicalExpected]; " +
            "WorkingDirectory=[$($shortcut.WorkingDirectory)]; link_bytes=$($linkBytes.Length); link_base64=$linkBase64")
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
    $outcome.checks += 'final-uninstall-retains-index'
    # Keep native-wizard verification mandatory, but run it after the independent
    # installation/upgrade/runtime/uninstall checks so a desktop-provider failure
    # still leaves evidence for those completed checks. A failure still fails CI.
    $outcome.chinese_wizard = Check-ChineseWizard $Installer
    Require (-not (Test-Path -LiteralPath $RegistryKey)) 'Cancelling the wizard unexpectedly installed the application'
    $outcome.checks += 'native-chinese-wizard-next-cancel'
    $outcome.status = 'passed'
} catch {
    $outcome.error = $_.ToString()
    throw
} finally {
    $env:QT_QPA_PLATFORM = $previousQtPlatform
    $outcome | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $WorkRoot 'installer-regression.json') -Encoding UTF8
}
Write-Host "Windows installer lifecycle and packaged regressions passed. Evidence: $WorkRoot"
