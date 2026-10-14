@echo off
rem Budget Pie launcher.
rem First run creates a local virtual environment and installs dependencies.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv || goto :nopython
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :pipfail
)

start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:nopython
echo Python 3.10+ was not found on PATH. Install it from python.org and retry.
pause
exit /b 1

:pipfail
echo Dependency installation failed. See the messages above.
pause
exit /b 1
