# Lead Finder - one-time setup for Windows.
# Downloads the Google Maps scraper (checksum-verified) and the browser it uses.
# Run it by double-clicking "Setup (run once).bat".

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # much faster downloads in Windows PowerShell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Version = '1.20.0'
$Asset   = "google_maps_scraper-$Version-windows-amd64.exe"
$Sha256  = 'fc828b0c2f42de191eef0f8695008da1479b35baaadabecfd7ef29a5b508b170'
$Url     = "https://github.com/gosom/google-maps-scraper/releases/download/v$Version/$Asset"

Set-Location -LiteralPath $PSScriptRoot

Write-Host "`n[1/3] Checking Python..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "Python 3 is not installed." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH', then run this setup again."
    exit 1
}
Write-Host "      found $($python.Source)"

Write-Host "`n[2/3] Getting the Google Maps scraper v$Version (about 60 MB)..."
if (Test-Path .\google-maps-scraper.exe) {
    Write-Host "      already downloaded"
} else {
    $tmp = Join-Path $env:TEMP $Asset
    Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $Sha256) {
        Remove-Item -LiteralPath $tmp -Force
        throw "Checksum mismatch: the download is not the official file. Nothing was installed."
    }
    Move-Item -LiteralPath $tmp -Destination .\google-maps-scraper.exe
    Write-Host "      downloaded and verified"
}

Write-Host "`n[3/3] Installing the browser the scraper uses (one time, a few hundred MB)..."
$env:PLAYWRIGHT_INSTALL_ONLY = '1'
$p = Start-Process -FilePath .\google-maps-scraper.exe -NoNewWindow -PassThru -Wait
Remove-Item Env:PLAYWRIGHT_INSTALL_ONLY
New-Item -ItemType Directory -Force -Path .\webdata | Out-Null

Write-Host "`nSetup finished. Double-click 'Open Lead Finder.bat' to start." -ForegroundColor Green
