@echo off
title Liberty Restoration - Inventory Bot Setup
color 0B
echo.
echo  ================================================
echo    Liberty Restoration - Inventory Bot Setup
echo  ================================================
echo.

:: Check for Python
python --version > nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed.
    echo.
    echo  Please install Python from https://python.org
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo  [1/3] Installing required packages...
pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to install packages.
    echo  Please contact your administrator.
    pause
    exit /b 1
)
echo         Done.
echo.

echo  [2/3] Creating desktop shortcut...
set PROJECT_DIR=%~dp0
set PROJECT_DIR=%PROJECT_DIR:~0,-1%
set LAUNCHER=%PROJECT_DIR%\Start Inventory Bot.bat

(
echo @echo off
echo cd /d "%PROJECT_DIR%"
echo python "%PROJECT_DIR%\app.py"
echo if errorlevel 1 ^(
echo     echo.
echo     echo An error occurred. Please contact your administrator.
echo     pause
echo ^)
) > "%LAUNCHER%"

copy "%LAUNCHER%" "%USERPROFILE%\Desktop\Inventory Bot.bat" > nul
echo         Shortcut created on Desktop.
echo.

echo  [3/3] Setup complete!
echo.
echo  ================================================
echo   You can now double-click "Inventory Bot"
echo   on your Desktop to launch the application.
echo  ================================================
echo.
pause
