@echo off
setlocal EnableExtensions
title BiliBili Lucky Draw
cd /d "%~dp0"

:menu
cls
echo ========================================
echo        BiliBili Lucky Draw
echo ========================================
echo.
echo 1. Initial setup
echo 2. Start application
echo 0. Exit
echo.
choice /c 120 /n /m "Select an option: "
if errorlevel 3 goto end
if errorlevel 2 goto start_application
if errorlevel 1 goto initial_setup
goto menu

:check_docker
docker version >nul 2>&1
if errorlevel 1 goto docker_error
exit /b 0

:docker_error
echo.
echo Docker is not available. Start Docker Desktop first.
pause
exit /b 1

:initial_setup
call :check_docker
if errorlevel 1 goto menu

if exist ".env" goto deploy
if not exist ".env.example" goto env_example_error

copy /Y ".env.example" ".env" >nul
if errorlevel 1 goto env_copy_error

set "generated_db_password=%RANDOM%%RANDOM%%RANDOM%%RANDOM%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%generated_db_password%'; $s=Get-Content -Raw -LiteralPath '.env'; $s=$s -replace '(?m)^MYSQL_PASSWORD=.*$','MYSQL_PASSWORD='+$p; $s=$s -replace '(?m)^MYSQL_ROOT_PASSWORD=.*$','MYSQL_ROOT_PASSWORD='+$p; Set-Content -LiteralPath '.env' -Value $s -Encoding utf8"
if errorlevel 1 goto env_password_error

echo.
echo Created .env from .env.example.
echo Database passwords were generated automatically.

:deploy
echo.
echo Building Docker images...
docker compose build
if errorlevel 1 goto build_error

echo.
echo Starting Docker services...
docker compose up -d
if errorlevel 1 goto start_error
goto show_status

:start_application
call :check_docker
if errorlevel 1 goto menu

echo.
echo Starting Docker services...
docker compose up -d
if errorlevel 1 goto start_error

:show_status
echo.
echo Container status:
docker compose ps
echo.
echo Admin console: http://127.0.0.1:8000
pause
goto menu

:env_example_error
echo.
echo .env.example was not found.
pause
goto menu

:env_copy_error
echo.
echo Failed to create .env.
pause
goto menu

:env_password_error
echo.
echo Failed to write database passwords to .env.
pause
goto menu

:build_error
echo.
echo Docker build failed.
pause
goto menu

:start_error
echo.
echo Docker services failed to start.
pause
goto menu

:end
endlocal
exit /b 0
