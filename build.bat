@echo off
echo ================================================
echo  Building InventoryBot.exe
echo ================================================
echo.

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found — installing...
    pip install pyinstaller
)

pyinstaller InventoryBot.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check errors above.
    pause
    exit /b 1
)

echo.
echo ================================================
echo  Done!  dist\InventoryBot.exe is ready.
echo  Drop it in the shared Google Drive folder.
echo ================================================
pause
