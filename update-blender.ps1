<#
.SYNOPSIS
    Blender portable version updater.
.DESCRIPTION
    Downloads, extracts and installs the latest (or specified) Blender portable
    version to C:\Programs\blender-<version>-windows-x64, then cleans up the zip.
.PARAMETER Version
    Blender version to install, e.g. "5.2.2". If omitted, auto-detects the
    latest LTS version from the Blender website.
.PARAMETER ProgramsDir
    Target directory for all Blender versions. Defaults to "C:\Programs".
.EXAMPLE
    .\update-blender.ps1
    # Auto-detect latest LTS, download & install
.EXAMPLE
    .\update-blender.ps1 -Version 5.2.2
    # Install a specific version
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$ProgramsDir = "C:\Programs"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = 'SilentlyContinue'

$Mirrors = @(
    "https://mirrors.aliyun.com/blender/release/Blender{0}/blender-{1}-windows-x64.zip",
    "https://mirrors.ocf.berkeley.edu/blender/release/Blender{0}/blender-{1}-windows-x64.zip",
    "https://mirror.freedif.org/blender/release/Blender{0}/blender-{1}-windows-x64.zip",
    "https://download.blender.org/release/Blender{0}/blender-{1}-windows-x64.zip"
)

# ── Step 1: Resolve version ──────────────────────────────────────────

if (-not $Version) {
    Write-Host "[1/5] Detecting latest LTS version from blender.org ..." -ForegroundColor Cyan
    $page = Invoke-WebRequest -Uri "https://www.blender.org/download/" -UseBasicParsing
    # match patterns like "Blender 5.2.2 LTS" or just "5.2.2"
    if ($page.Content -match 'Blender\s+(\d+\.\d+\.\d+)\s+LTS') {
        $Version = $Matches[1]
    } elseif ($page.Content -match 'blender-(\d+\.\d+\.\d+)-windows') {
        $Version = $Matches[1]
    } else {
        Write-Host "Could not auto-detect version. Please specify with -Version." -ForegroundColor Red
        exit 1
    }
}

$MajorMinor = ($Version -split '\.')[0..1] -join ''
$DirName    = "blender-$Version-windows-x64"
$InstallPath = Join-Path $ProgramsDir $DirName
$ExePath     = Join-Path $InstallPath "blender.exe"

Write-Host "Target version : $Version" -ForegroundColor Green
Write-Host "Install path   : $InstallPath" -ForegroundColor Green

# ── Step 2: Check if already installed ────────────────────────────────

if (Test-Path $ExePath) {
    Write-Host "[skip] $DirName already exists at $InstallPath" -ForegroundColor Yellow
    $continue = Read-Host "Reinstall? (y/N)"
    if ($continue -ne 'y') { exit 0 }
    Write-Host "Removing existing installation ..." -ForegroundColor DarkYellow
    Remove-Item $InstallPath -Recurse -Force
}

# ── Step 3: Download ──────────────────────────────────────────────────

Write-Host "[2/5] Downloading Blender $Version portable zip ..." -ForegroundColor Cyan

$TempDir  = [System.IO.Path]::GetTempPath()
$ZipFile  = Join-Path $TempDir "blender-$Version-windows-x64.zip"
$Downloaded = $false

foreach ($tmpl in $Mirrors) {
    $url = $tmpl -f $MajorMinor, $Version
    Write-Host "  Trying: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $ZipFile -UseBasicParsing -TimeoutSec 600
        $size = (Get-Item $ZipFile).Length
        if ($size -gt 1000000) {
            $sizeMB = [math]::Round($size / 1MB, 2)
            Write-Host "  Download OK ($sizeMB MB)" -ForegroundColor Green
            $Downloaded = $true
            break
        } else {
            Write-Host "  File too small ($size bytes), skipping" -ForegroundColor DarkYellow
            Remove-Item $ZipFile -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Host "  Failed: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
}

if (-not $Downloaded) {
    Write-Host "Download failed from all mirrors." -ForegroundColor Red
    exit 1
}

# ── Step 4: Extract ───────────────────────────────────────────────────

Write-Host "[3/5] Extracting to $ProgramsDir ..." -ForegroundColor Cyan
Expand-Archive -Path $ZipFile -DestinationPath $ProgramsDir -Force

if (-not (Test-Path $ExePath)) {
    Write-Host "blender.exe not found after extraction: $ExePath" -ForegroundColor Red
    exit 1
}
Write-Host "  Extraction OK: $ExePath" -ForegroundColor Green

# ── Step 5: Cleanup ───────────────────────────────────────────────────

Write-Host "[4/5] Removing downloaded zip ..." -ForegroundColor Cyan
Remove-Item $ZipFile -Force
Write-Host "  Zip deleted." -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────────────

Write-Host "[5/5] Verifying ..." -ForegroundColor Cyan
$result = & $ExePath --version 2>&1 | Select-Object -First 1
Write-Host "  $result" -ForegroundColor Green

Write-Host ""
Write-Host "Done! Blender $Version portable installed at:" -ForegroundColor Cyan
Write-Host "  $InstallPath" -ForegroundColor White
Write-Host ""
Write-Host "Existing Blender versions in $ProgramsDir :" -ForegroundColor DarkCyan
Get-ChildItem $ProgramsDir -Directory | Where-Object { $_.Name -like "blender-*-windows-x64" } |
    Select-Object Name | Format-Table -AutoSize
