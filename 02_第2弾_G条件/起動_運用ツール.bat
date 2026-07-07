@echo off
rem ============================================================
rem  Gaia Bugakari Test-Case Tool launcher
rem   - starts local web server (webapp/server.py)
rem   - the server itself opens the browser after it starts
rem   - dev: uses python on PATH.
rem     For distribution, set PY to the bundled embed python
rem     (e.g. python-embed\python.exe).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

rem --- find Python (prefer py launcher, fallback to python) ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found.
  echo   Install Python, or use the bundled embed python for distribution.
  pause
  exit /b 1
)

echo Starting server: %PY% webapp\server.py
echo (This black window IS the server. Close it to stop the tool.)
echo A browser will open automatically at http://127.0.0.1:8765/
%PY% "%~dp0webapp\server.py"

echo.
echo Server stopped.
pause
