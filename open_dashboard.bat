@echo off
start /B "" "%~dp0.venv\Scripts\pythonw.exe" -m goldbot.cli dashboard-live --ledger reports/demo.execution.ledger.sqlite --profile config/profiles/micro_50.yaml --host 127.0.0.1 --port 8010 > "%~dp0reports\dashboard-server.out.log" 2> "%~dp0reports\dashboard-server.err.log"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8010/"
