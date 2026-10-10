@echo off
setlocal
cd /d "%~dp0"
set "ALEX_PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "ALEX_STUDY_ENV=%~dp0..\.venv-alex-study"
set "PYTHONUTF8=1"
if not exist "%ALEX_PY%" (
  echo Python 3.11 was not found at %ALEX_PY%
  echo Read study\JUPYTERLAB_GUIDE.md for setup instructions.
  pause
  exit /b 1
)
if not exist "%ALEX_STUDY_ENV%\Scripts\python.exe" (
  echo Creating the study environment...
  "%ALEX_PY%" -m venv "%ALEX_STUDY_ENV%"
  if errorlevel 1 goto failed
)
"%ALEX_STUDY_ENV%\Scripts\python.exe" -c "import jupyterlab, ipykernel" >nul 2>&1
if errorlevel 1 (
  echo Installing JupyterLab in the study environment. This may take a few minutes...
  "%ALEX_STUDY_ENV%\Scripts\python.exe" -m pip install jupyterlab
  if errorlevel 1 goto failed
)
echo Opening JupyterLab in the study folder...
"%ALEX_STUDY_ENV%\Scripts\python.exe" -m jupyterlab --notebook-dir="%CD%"
if errorlevel 1 goto failed
exit /b 0
:failed
echo JupyterLab setup or launch failed. Check the error above.
pause
exit /b 1
