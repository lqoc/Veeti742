@echo off
rem Starts the Grok Bot desk + Cloudflare tunnel (PAPER mode). GROK-BOT.bat calls this; you can also double-click it.
cd /d "%~dp0\..\.."
title Grok Bot setup
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist .venv\Scripts\python.exe (
  echo Creating Python environment...
  %PY% -m venv .venv || goto nopython
)
echo Installing packages...
.venv\Scripts\python -m pip install -q --disable-pip-version-check --upgrade pip
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || goto pipfail
if not exist tools mkdir tools
if not exist tools\cloudflared.exe (
  echo Downloading cloudflared...
  curl -L -s -o tools\cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe || goto cffail
)
set HOST=127.0.0.1
set PORT=8000
set TUNNEL_METRICS=http://127.0.0.1:2000
set DESK_SITE=deed
set DESK_PRESET=sniper
start "Grok Bot desk (keep open)" /min cmd /k .venv\Scripts\python -m desk.main
timeout /t 6 /nobreak >nul
start "Grok Bot tunnel (keep open)" /min cmd /k tools\cloudflared.exe tunnel --no-autoupdate --metrics 127.0.0.1:2000 --url http://127.0.0.1:8000
echo Started. Waiting for the tunnel...
timeout /t 20 /nobreak >nul
if not defined GROKBOT_AUTO call "%~dp0LOGIN-LINK.bat"
exit /b 0

:nopython
echo Python 3.12 is missing. Run GROK-BOT.bat, it installs Python for you.
if not defined GROKBOT_AUTO pause
exit /b 1
:pipfail
echo Package install failed (internet?).
if not defined GROKBOT_AUTO pause
exit /b 1
:cffail
echo cloudflared download failed (internet?).
if not defined GROKBOT_AUTO pause
exit /b 1
