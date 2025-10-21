@echo off
title JOAT Discord Bot
echo ================================================
echo              Initialising Helix ...
echo ================================================
echo.

REM Navigate to the bot directory
cd /d "%~dp0"

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Start the bot
python bot.py

echo.
echo ================================================
echo Bot stopped or exited.
echo Press any key to close this window...
pause >nul
