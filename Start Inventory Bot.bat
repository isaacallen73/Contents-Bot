@echo off
cd /d "G:\My Drive\Inventory Bot"
python "G:\My Drive\Inventory Bot\app.py"
if errorlevel 1 (
    echo.
    echo An error occurred. Please contact your administrator.
    pause
)
