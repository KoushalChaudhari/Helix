@echo off
title Helix Bot
cd /d "%~dp0"

echo ======================================
echo 💠 Launching Helix Discord Bot...
echo ======================================
echo.

:: OPTIONAL: Activate virtual environment if you have one
:: call venv\Scripts\activate

python bot.py

echo.
echo ======================================
echo 💜 Helix has stopped or closed.
echo If you see an error above, screenshot it for debugging.
echo Press any key to exit...
echo ======================================
pause >nul
