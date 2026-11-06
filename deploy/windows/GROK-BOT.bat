@echo off
rem ======================================================================
rem  GROK-BOT.bat  -  fully automatic, no keys to press:
rem    1. installs Python 3.12 if missing (silent)        4. stops the old copy only if something changed
rem    2. downloads the newest Grok Bot from GitHub        5. starts the desk + tunnel in PAPER mode
rem    3. extracts it to C:\GrokBot (your data is kept)    6. sets itself to start at boot/logon + update hourly
rem  Your links are saved to "Grok Bot links.txt" on the Desktop and opened in Notepad.
rem  Run it again any time to update.
rem ======================================================================
setlocal
title Grok Bot
set "APP=C:\GrokBot"
set "ZIP_URL=https://github.com/lqoc/Veeti742/archive/refs/heads/ccr-8f54846f-6j3ph7.zip"
set "ZIP=%TEMP%\grokbot.zip"
set "UNZ=%TEMP%\grokbot-dl"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "GROKBOT_AUTO=1"
set "QUIET="
if /i "%~1"=="auto" set "QUIET=1"
set "LINKS=%USERPROFILE%\Desktop\Grok Bot links.txt"
if not exist "%USERPROFILE%\Desktop" set "LINKS=%APP%\Grok Bot links.txt"
if not exist "%APP%" mkdir "%APP%"
set "LOG=%APP%\grokbot-setup.log"
echo ==== %DATE% %TIME% > "%LOG%"

echo [1/6] Checking Python...
set "PY="
if exist "%APP%\.venv\Scripts\python.exe" set "PY=%APP%\.venv\Scripts\python.exe"
if defined PY goto have_py
py -3.12 -c "print()" >nul 2>nul && set "PY=py -3.12"
if defined PY goto have_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto have_py
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto have_py
if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if defined PY goto have_py
echo       Installing Python 3.12 silently, about 1-2 minutes...
curl -L -s -o "%TEMP%\python-3.12.10-amd64.exe" "%PYURL%" >>"%LOG%" 2>&1 || goto fail
"%TEMP%\python-3.12.10-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 >>"%LOG%" 2>&1 || goto fail
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" goto fail
:have_py
echo       using %PY%>>"%LOG%"

echo [2/6] Downloading the newest Grok Bot...
curl -L -s -o "%ZIP%" "%ZIP_URL%" >>"%LOG%" 2>&1 || goto fail
if exist "%UNZ%" rmdir /s /q "%UNZ%"
mkdir "%UNZ%"
tar -xf "%ZIP%" -C "%UNZ%" >>"%LOG%" 2>&1 || goto fail

echo [3/6] Installing to %APP% ...
robocopy "%UNZ%\Veeti742-ccr-8f54846f-6j3ph7" "%APP%" /E /XD data .venv tools /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto fail
copy /y "%APP%\deploy\windows\GROK-BOT.bat" "%APP%\GROK-BOT.bat" >nul

set "RUNNING="
curl -s -m 3 -o nul http://127.0.0.1:8000/healthz >nul 2>nul && set "RUNNING=1"
if not defined RUNNING goto not_running
if "%RC%"=="0" goto up_to_date
echo [4/6] New version: stopping the old copy...
taskkill /im cloudflared.exe /f >>"%LOG%" 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*desk.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >>"%LOG%" 2>&1
timeout /t 3 /nobreak >nul
goto start
:up_to_date
echo [4/6] Already up to date and running.
goto tasks
:not_running
echo [4/6] Not running yet.

:start
if exist "%APP%\.venv\Scripts\python.exe" goto have_venv
echo       Creating the Python environment...
%PY% -m venv "%APP%\.venv" >>"%LOG%" 2>&1 || goto fail
:have_venv
echo [5/6] Installing packages and starting Grok Bot in PAPER mode (first time 1-3 minutes)...
call "%APP%\deploy\windows\START-GROK-BOT.bat" || goto fail

:tasks
echo [6/6] Setting up start at boot and hourly updates...
rem as administrator: real boot start + hourly update, no login needed
schtasks /create /tn "GrokBot start" /sc onstart /ru SYSTEM /rl highest /f /tr "cmd /c \"%APP%\GROK-BOT.bat\" auto" >>"%LOG%" 2>&1 && schtasks /create /tn "GrokBot update" /sc hourly /ru SYSTEM /rl highest /f /tr "cmd /c \"%APP%\GROK-BOT.bat\" auto" >>"%LOG%" 2>&1 && goto links
rem not administrator: start when you log in + hourly update while logged in
schtasks /create /tn "GrokBot update" /sc hourly /f /tr "cmd /c \"%APP%\GROK-BOT.bat\" auto" >>"%LOG%" 2>&1
(echo @echo off& echo start "" /min cmd /c "%APP%\GROK-BOT.bat" auto) > "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GrokBot.bat"

:links
pushd "%APP%"
set /a TRIES=0
:waitlink
"%APP%\.venv\Scripts\python.exe" -m desk.main --print-login > "%TEMP%\grokbot-link.txt" 2>>"%LOG%"
findstr /c:"YOUR-TUNNEL-URL" "%TEMP%\grokbot-link.txt" >nul || goto gotlink
set /a TRIES+=1
if %TRIES% GEQ 20 goto gotlink
timeout /t 5 /nobreak >nul
goto waitlink
:gotlink
popd
(
  echo Grok Bot is running in PAPER mode on this VM.
  echo Open the links on your phone in Chrome or Samsung Internet. Keep them private.
  echo.
  type "%TEMP%\grokbot-link.txt"
  echo.
  echo The trycloudflare link is the full app: Live/Paper, settings, wallet, export.
  echo The deed.page link is the read-only website. Its address never changes.
  echo The trycloudflare address changes when the VM restarts: run GROK-BOT.bat or LOGIN-LINK.bat for the new one.
  echo Keep the "desk" and "tunnel" windows open.
) > "%LINKS%"
type "%LINKS%"
if not defined QUIET start "" notepad "%LINKS%"
exit /b 0

:fail
echo.
echo Something failed. The log opens now: send it (or a photo) to Claude.
if not defined QUIET start "" notepad "%LOG%"
exit /b 1
