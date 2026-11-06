@echo off
rem Downloads the latest Grok Bot code over this folder. Keeps your data (data\), Python (.venv\) and tools\.
cd /d "%~dp0\..\.."
set "ROOT=%cd%"
set "NEW=%TEMP%\grokbot-update"
echo Downloading the latest Grok Bot...
curl -L -o "%TEMP%\grokbot.zip" https://github.com/lqoc/Veeti742/archive/refs/heads/ccr-8f54846f-6j3ph7.zip || goto fail
if not exist "%NEW%" mkdir "%NEW%"
tar -xf "%TEMP%\grokbot.zip" -C "%NEW%" || goto fail
robocopy "%NEW%\Veeti742-ccr-8f54846f-6j3ph7" "%ROOT%" /E /XD data .venv tools /NFL /NDL /NJH /NJS /NP
echo.
echo Updated. Close the two Grok Bot windows ("desk" and "tunnel"), then double-click START-GROK-BOT.bat.
pause
exit /b 0
:fail
echo Update failed. Check the internet connection and try again.
pause
exit /b 1
