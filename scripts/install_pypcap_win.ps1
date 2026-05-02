# Install pypcap on Windows for HotWire EVSE emulator.
#
# Requires Npcap SDK (downloaded below) + MSVC C++ compiler.
# Targets the project venv at C:\Users\USER\HotWire\.venv.

$ErrorActionPreference = 'Stop'

$HotWireRoot = 'C:\Users\USER\HotWire'
$SDKZip = "$env:TEMP\npcap-sdk.zip"
$SDKDir = "$HotWireRoot\_npcap_sdk"
$SDKUrl = 'https://npcap.com/dist/npcap-sdk-1.15.zip'

Write-Host "=== Step 1: Download Npcap SDK ===" -ForegroundColor Cyan
if (-not (Test-Path $SDKZip)) {
    Write-Host "Downloading Npcap SDK from $SDKUrl ..."
    Invoke-WebRequest -Uri $SDKUrl -OutFile $SDKZip -UseBasicParsing
} else {
    Write-Host "Already downloaded: $SDKZip"
}

Write-Host "`n=== Step 2: Extract SDK ===" -ForegroundColor Cyan
if (Test-Path $SDKDir) {
    Remove-Item -Recurse -Force $SDKDir
}
Expand-Archive -Path $SDKZip -DestinationPath $SDKDir -Force
Write-Host "Extracted to: $SDKDir"
Get-ChildItem $SDKDir | Select-Object Name | Format-Table

Write-Host "`n=== Step 3: Find MSVC ===" -ForegroundColor Cyan
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) {
    Write-Error 'vswhere.exe not found'
    exit 2
}
$vsPath = & $vswhere -latest -property installationPath
Write-Host "VS install: $vsPath"

$vcVarsBat = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcVarsBat)) {
    Write-Error "vcvars64.bat missing under $vsPath. Install 'Desktop development with C++' workload."
    exit 3
}
Write-Host "vcvars64.bat: $vcVarsBat"

Write-Host "`n=== Step 4: Install pypcap via cmd wrapper (so vcvars env is loaded) ===" -ForegroundColor Cyan
$venvPy = "$HotWireRoot\.venv\Scripts\python.exe"
$incDir = "$SDKDir\Include"
$libDir = "$SDKDir\Lib\x64"

if (-not (Test-Path $incDir)) {
    Write-Error ("Npcap SDK missing Include dir - extraction path wrong. Listing " + $SDKDir)
    Get-ChildItem $SDKDir | Format-Table
    exit 4
}

$cmdScript = @"
call "$vcVarsBat"
set INCLUDE=$incDir;%INCLUDE%
set LIB=$libDir;%LIB%
"$venvPy" -m pip install --no-cache-dir pypcap
"@
$tmpBat = "$env:TEMP\pypcap_install.bat"
Set-Content -Path $tmpBat -Value $cmdScript -Encoding ASCII

Write-Host "Running: $tmpBat"
& cmd /c $tmpBat
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Write-Error "pip install pypcap failed rc=$rc"
    exit $rc
}

Write-Host "`n=== Step 5: Verify import ===" -ForegroundColor Cyan
& $venvPy -c "import pcap; print('pypcap OK, version =', getattr(pcap, '__version__', '?'))"
