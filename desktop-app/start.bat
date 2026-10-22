@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run build.ps1 first to install dependencies.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" desktop.py
