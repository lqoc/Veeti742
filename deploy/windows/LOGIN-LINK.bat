@echo off
rem Prints your Grok Bot links (website + login) and saves them to the Desktop. Keep them private.
cd /d "%~dp0\..\.."
.venv\Scripts\python -m desk.main --print-login > "%USERPROFILE%\Desktop\Grok Bot links.txt" 2>nul
echo.
type "%USERPROFILE%\Desktop\Grok Bot links.txt"
echo.
echo Saved to "Grok Bot links.txt" on the Desktop. If it shows YOUR-TUNNEL-URL, wait 30 seconds and run this again.
if not defined GROKBOT_AUTO start "" notepad "%USERPROFILE%\Desktop\Grok Bot links.txt"
