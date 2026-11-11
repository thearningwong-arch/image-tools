[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$AppDir,
    [string]$OutputDir,
    [string]$IsccPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $AppDir) { $AppDir = Join-Path $repo 'dist\ImageTools' }
if (-not $OutputDir) { $OutputDir = Join-Path $repo 'dist' }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
    throw 'Version must have three or four numeric components.'
}
if (-not (Test-Path -LiteralPath (Join-Path $AppDir 'ImageTools.exe') -PathType Leaf)) {
    throw "Windows application bundle is missing: $AppDir\ImageTools.exe"
}
if (-not $IsccPath) { $IsccPath = $env:ISCC_EXE }
if (-not $IsccPath) {
    $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($onPath) { $IsccPath = $onPath.Source }
}
if (-not $IsccPath) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Install Inno Setup 6.5+ from https://jrsoftware.org/isdl.php, or pass -IsccPath / set ISCC_EXE.'
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$installer = Join-Path $OutputDir "ImageTools-$Version-win-x64-setup.exe"
# A failed compile must never leave a previous run's installer looking current.
if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }
$logDir = Join-Path $repo 'build\test-results'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$compilerOutput = & $IsccPath "/DAppVersion=$Version" "/DBuildRoot=$AppDir" "/DOutputRoot=$OutputDir" (Join-Path $repo 'installer\image-tools.iss') 2>&1
$compilerExitCode = $LASTEXITCODE
$compilerOutput | ForEach-Object { Write-Host $_ }
$compilerOutput | Set-Content -LiteralPath (Join-Path $logDir "installer-$Version.log") -Encoding UTF8
if ($compilerExitCode -ne 0) {
    $details = ($compilerOutput -join "`n").Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
    Write-Output "::error title=Inno Setup compilation::$details"
    throw "Inno Setup failed with exit code $compilerExitCode"
}
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installer was not produced: $installer" }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$installer.sha256", "$hash  $([IO.Path]::GetFileName($installer))`n", [Text.UTF8Encoding]::new($false))
Write-Host "Installer: $installer"
Write-Host "SHA-256:   $hash"
