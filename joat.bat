@echo off
title JOAT Discord Bot
echo ================================================
echo         Launching JOAT Discord Bot...
echo ================================================
echo.

REM Navigate to the bot directory (change path if needed)
cd /d "%~dp0"

REM Activate the virtual environment
call .venv\Scripts\activate.bat

REM Start the bot
python bot.py

REM Keep the window open if bot crashes or stops
echo.
echo ================================================
echo Bot stopped or exited.
echo Press any key to close this window...
pause >nul
