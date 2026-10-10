@echo off
rem Connect this laptop to the ALEX on your desktop (ADR-052). ASCII only: cp949 breaks Korean in .cmd.
rem   connect_desktop.cmd            connect and open the browser
rem   connect_desktop.cmd setup ...  first time: make a key and save the desktop name
rem   connect_desktop.cmd check      what is ready
setlocal
cd /d "%~dp0"
set "ALEX_PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%ALEX_PY%" set "ALEX_PY=python"
set "PYTHONUTF8=1"
"%ALEX_PY%" -m jarvis.remote %*
echo.
pause
