# Launch pyPLC on Windows with all three workarounds applied.
#
# Without these the process exits ~0.4 s after start and the console
# window closes mid-buffer, making it look like it "stopped at
# 'listening on port 57122'".
#
#   1. PYTHONIOENCODING=utf-8
#       Avoids UnicodeEncodeError when Arduino serial bytes contain
#       high-bit values that the default cp950 codec can't encode.
#
#   2. python -u
#       Unbuffered stdout so the operator can see every trace line in
#       real time, not just the ones that happened to flush before a
#       crash.
#
#   3. Transcript to timestamped log under logs\
#       Captures stderr too in case something still crashes — that
#       lets you diff against the pyplc.ini to see which field broke.
#
# Assumes udplog.py and pyPlcHomeplug.py have been patched with the
# diffs from patches/pyplc/*.patch in this tree. See README.md for
# the three-patch rationale.
#
# Usage:
#   cd C:\Users\USER\pyPLC
#   .\run_pyplc_windows.ps1 E        # EVSE mode
#   .\run_pyplc_windows.ps1 P        # PEV mode
#   .\run_pyplc_windows.ps1 L        # Listener mode

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('E', 'P', 'L')]
    [string]$Mode
)

# UTF-8 for Python stdout/stderr so serial noise can't crash print().
$env:PYTHONIOENCODING = 'utf-8'

# Timestamped log, written alongside pyPlc.py.
$logsDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}
$stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$logFile = Join-Path $logsDir "pyplc_${Mode}_${stamp}.log"

Write-Host "Launching pyPLC in mode $Mode" -ForegroundColor Cyan
Write-Host "Log file: $logFile" -ForegroundColor Cyan
Write-Host "Press Ctrl-C to stop." -ForegroundColor Cyan
Write-Host ""

# -u = unbuffered; stream to tee so operator sees live output AND we
# get a persistent log. tee requires PowerShell 5+ 'Tee-Object'.
python -u pyplc.py $Mode 2>&1 | Tee-Object -FilePath $logFile
