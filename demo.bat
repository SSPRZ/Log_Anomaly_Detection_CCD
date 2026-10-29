@echo off
rem One-click live demo: starts the platform in real time plus the dashboard, then opens the browser.
rem Inject faults from the dashboard's "Demo controls" panel. Close the two windows to stop.
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "LAP__DETECTION__REGISTRY_DIR=%~dp0data\demo\models"
set "LAP__DETECTION__MLFLOW__ENABLED=false"
set "LAP_FAULT_API=http://localhost:8091"

if not exist "%~dp0data\demo\models\registry.json" (
  echo No trained model yet: training one first ^(about 10 minutes, only once^)...
  "%PY%" lap.py demo || exit /b 1
)

if exist "%~dp0data\live" rmdir /s /q "%~dp0data\live"
mkdir "%~dp0data\live"
set "LAP__STORAGE__SQLITE_PATH=%~dp0data\live\lap.db"

echo Starting the live platform (fault injector on http://localhost:8091) ...
start "LAP live platform" "%PY%" lap.py live --api-port 8091 --metrics-port 9118
timeout /t 8 /nobreak >nul

echo Starting the dashboard on http://localhost:8502 ...
start "LAP dashboard" "%PY%" lap.py dashboard --db "%~dp0data\live\lap.db" --registry "%~dp0data\demo\models" --port 8502 --fault-api http://localhost:8091
timeout /t 15 /nobreak >nul
start "" http://localhost:8502

echo.
echo Demo is running. Give it 2-3 minutes of normal traffic, then use "Demo controls" in the dashboard sidebar.
echo Close the "LAP live platform" and "LAP dashboard" windows to stop.
