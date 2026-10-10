@echo off
cd /d "%~dp0"
set "JARVIS_USE_OLLAMA=0"
if "%JARVIS_FILE_SEARCH_ROOT%"=="" set "JARVIS_FILE_SEARCH_ROOT=%CD%"
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  set "PY=python"
) else (
  set "PY=py -3"
)
%PY% -m jarvis
pause
