@echo off
REM RAHAT: one double-click starts the backend + dashboard and opens the browser.
REM Close both black windows to stop everything.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. First time setup:
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo     cd frontend ^&^& npm install
  pause
  exit /b 1
)

start "RAHAT backend (port 8000)" cmd /k ".venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
start "RAHAT dashboard (port 5173)" cmd /k "cd frontend && npm run dev"

echo Starting RAHAT... browser opens in a few seconds.
timeout /t 7 /nobreak >nul
start "" "http://localhost:5173"
