@echo off
rem Run MedOS on this Windows laptop (GPIO is simulated). Opens http://localhost:8080
cd /d "%~dp0"
python -m pip install --quiet -r requirements.txt
start "" http://localhost:8080
python run.py --port 8080
pause
