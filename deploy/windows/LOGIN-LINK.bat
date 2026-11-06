@echo off
rem Prints the one-time login link for your phone. Keep it private.
cd /d "%~dp0\..\.."
echo.
echo Your Grok Bot login link (open it once on your phone in Chrome or Samsung Internet):
.venv\Scripts\python -m desk.main --print-login
echo.
echo If it shows YOUR-TUNNEL-URL, wait 30 seconds and double-click LOGIN-LINK.bat again.
pause
