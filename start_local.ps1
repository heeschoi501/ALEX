Set-Location $PSScriptRoot
$env:JARVIS_USE_OLLAMA = "1"
if (-not $env:JARVIS_FILE_SEARCH_ROOT) { $env:JARVIS_FILE_SEARCH_ROOT = (Get-Location).Path }
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyExe = "python"
    $pyArgs = @()
} else {
    $pyExe = "py"
    $pyArgs = @("-3")
}
Write-Host "=== Project JARVIS local environment check ==="
& $pyExe @pyArgs scripts/check_environment.py
Write-Host "`n=== Starting Project JARVIS ==="
& $pyExe @pyArgs -m jarvis
