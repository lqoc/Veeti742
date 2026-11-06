@echo off
rem Grok Bot desk on Windows: Python + the desk + a Cloudflare quick tunnel. Double-click to start.
cd /d "%~dp0\..\.."
title Grok Bot setup
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo Creating Python environment...
  %PY% -m venv .venv || (echo Python 3.12 is missing. Install it from python.org, tick "Add python.exe to PATH". & pause & exit /b 1)
)
echo Installing packages (first time takes 1-2 minutes)...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
if not exist tools mkdir tools
if not exist tools\cloudflared.exe (
  echo Downloading cloudflared...
  curl -L -o tools\cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)
set HOST=127.0.0.1
set PORT=8000
set TUNNEL_METRICS=http://127.0.0.1:2000
start "Grok Bot desk (keep open)" cmd /k .venv\Scripts\python -m desk.main
timeout /t 6 /nobreak >nul
start "Grok Bot tunnel (keep open)" cmd /k tools\cloudflared.exe tunnel --no-autoupdate --metrics 127.0.0.1:2000 --url http://127.0.0.1:8000
echo Waiting for the tunnel...
timeout /t 25 /nobreak >nul
call "%~dp0LOGIN-LINK.bat"
