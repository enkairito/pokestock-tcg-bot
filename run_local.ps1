$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Get-Content (Join-Path $root ".env") | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}

& (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "check_stock.py")
