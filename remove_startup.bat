@echo off
rem Removes the Startup shortcut created by install_startup.bat.
setlocal
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Budget Pie.lnk"

if exist "%LNK%" (
    del "%LNK%"
    echo Removed - Budget Pie will no longer start with Windows.
) else (
    echo No startup shortcut found; nothing to remove.
)
pause
