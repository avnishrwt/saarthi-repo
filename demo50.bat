@echo off
REM RAHAT demo on the 50 synthetic Dehradun messages (data/demo_scenario_dehradun50.json).
REM Starts backend + dashboard if needed, RESETS the demo data, sends the flood alert, replays 50 messages.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run the first-time setup from README.md first.
  pause
  exit /b 1
)

REM start the servers only if they are not running yet
curl.exe -s -o nul http://localhost:8000/state
if errorlevel 1 start "RAHAT backend (port 8000)" cmd /k ".venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
curl.exe -s -o nul http://localhost:5173/
if errorlevel 1 start "RAHAT dashboard (port 5173)" cmd /k "cd frontend && npm run dev"

echo Waiting for the backend...
:wait
timeout /t 2 /nobreak >nul
curl.exe -s -o nul http://localhost:8000/state
if errorlevel 1 goto wait

start "" "http://localhost:5173"
timeout /t 4 /nobreak >nul

echo Resetting demo data...
curl.exe -s -o nul -X POST http://localhost:8000/sim/reset -H "Content-Type: application/json" -d "{}"
echo Sending the (simulated) IMD flood alert...
curl.exe -s -o nul -X POST http://localhost:8000/sim/alert -H "Content-Type: application/json" -d "{}"
echo Replaying 50 synthetic messages (about 50 seconds)...
curl.exe -s -X POST http://localhost:8000/sim/replay -H "Content-Type: application/json" -d "{\"speed\":4,\"scenario\":\"demo_scenario_dehradun50.json\"}"
echo.
echo Done. Watch the dashboard: cases keep arriving for about 50 seconds.
pause
