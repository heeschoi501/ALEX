@echo off
setlocal
cd /d "%~dp0"
set "ALEX_PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PYTHONUTF8=1"
if not exist "%ALEX_PY%" (
  echo Python 3.11 is required at: %ALEX_PY%
  echo Read START_HERE_DESKTOP.md for installation instructions.
  if /i not "%~1"=="--verify-only" pause
  exit /b 1
)
echo Verifying ALEX with Windows Python 3.11...
"%ALEX_PY%" scripts\verify_release.py
if errorlevel 1 (
  echo Verification failed. Keep this output for diagnosis.
  if /i not "%~1"=="--verify-only" pause
  exit /b 1
)
if /i "%~1"=="--verify-only" exit /b 0
echo Starting ALEX. The launcher will offer the required local downloads.
"%ALEX_PY%" -m jarvis.launcher
if errorlevel 1 (
  echo Launch failed. Check logs\web-server.log.
  pause
  exit /b 1
)
exit /b 0
