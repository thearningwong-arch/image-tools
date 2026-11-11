[CmdletBinding()]
param(
    [string]$Version,
    [string]$PythonExe,
    [string]$IsccPath,
    [string]$AppDir,
    [string]$OutputDir,
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$SkipAppBuild
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Build the Windows installer on Windows; Linux binaries cannot be packaged for Windows.'
}
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $repo
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}
if (-not $PythonExe) {
    $PythonExe = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            Invoke-Checked 'py.exe' @('-3', '-m', 'venv', '.venv')
        } else {
            Invoke-Checked 'python.exe' @('-m', 'venv', '.venv')
        }
    }
}
Invoke-Checked $PythonExe @('-c', "import sys, struct; assert (3,12) <= sys.version_info[:2] < (3,16), 'Python 3.12-3.15 required'; assert struct.calcsize('P') == 8, '64-bit Python required'")
if (-not $Version) {
    $Version = & $PythonExe -c 'from image_tools import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read application version.' }
    $Version = $Version.Trim()
}
if (-not $AppDir) { $AppDir = Join-Path $repo 'dist\ImageTools' }
if (-not $OutputDir) { $OutputDir = Join-Path $repo 'dist' }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
if (-not $SkipAppBuild) {
    if (-not $SkipInstall) {
        Invoke-Checked $PythonExe @('-m', 'pip', 'install', '-r', 'requirements-dev.txt')
    }
    Invoke-Checked $PythonExe @('-m', 'pip', 'check')
    if (-not $SkipTests) {
        New-Item -ItemType Directory -Path (Join-Path $repo 'build\test-results') -Force | Out-Null
        $previousPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:QT_QPA_PLATFORM = 'offscreen'
            Invoke-Checked $PythonExe @('-m', 'pytest', '-q', '-ra', '--junitxml=build/test-results/pytest.xml')
        } finally {
            $env:QT_QPA_PLATFORM = $previousPlatform
        }
    }
    if ([IO.Path]::GetFileName($AppDir) -ne 'ImageTools') {
        throw '-AppDir must end in ImageTools when building the application.'
    }
    Invoke-Checked $PythonExe @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'ImageTools', '--paths', '.', '--distpath', (Split-Path -Parent $AppDir),
        '--collect-all', 'psd_tools', '--collect-all', 'pypdfium2',
        '--collect-all', 'pypdfium2_raw', 'scripts\desktop_entry.py'
    )
}
& (Join-Path $PSScriptRoot 'windows\compile-installer.ps1') -Version $Version -AppDir $AppDir -OutputDir $OutputDir -IsccPath $IsccPath
