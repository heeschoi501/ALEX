Start-Process -FilePath "wscript.exe" -ArgumentList ('"' + (Join-Path $PSScriptRoot "start_alex.vbs") + '"') -WindowStyle Hidden
