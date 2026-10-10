@echo off
cd /d "%~dp0"
set "JARVIS_USE_OLLAMA=1"
if "%JARVIS_FILE_SEARCH_ROOT%"=="" set "JARVIS_FILE_SEARCH_ROOT=%CD%"
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  set "PY=python"
) else (
  set "PY=py -3"
)
echo === Project JARVIS local environment check ===
%PY% scripts\check_environment.py
if %ERRORLEVEL% NEQ 0 echo [WARN] Environment check reported a problem. JARVIS will still start for diagnostics.
echo.
echo === Starting Project JARVIS ===
%PY% -m jarvis
pause
