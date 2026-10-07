@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m loreforge ui
if errorlevel 1 pause
