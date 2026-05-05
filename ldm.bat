@echo off
setlocal enabledelayedexpansion

:: Liferay Docker Python Wrapper (Windows)
:: This script ensures a Python virtual environment (venv) is setup and runs the manager

set SCRIPT_DIR=%~dp0
set VENV_PATH=%SCRIPT_DIR%.venv
set PYTHON_SCRIPT=%SCRIPT_DIR%liferay_docker.py

:: Check for python.exe
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Error: Python is not installed.
    exit /b 1
)

:: Create venv if it doesn't exist
if not exist "%VENV_PATH%" (
    echo Creating virtual environment in %VENV_PATH%...
    python -m venv "%VENV_PATH%" || exit /b 1
    "%VENV_PATH%\Scripts\pip.exe" install --upgrade pip
    if exist "%SCRIPT_DIR%requirements.txt" (
        "%VENV_PATH%\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt"
    )
)

:: Automated Pre-commit setup
if exist "%SCRIPT_DIR%.git" (
    if exist "%VENV_PATH%\Scripts\pre-commit.exe" (
        if not exist "%SCRIPT_DIR%.git\hooks\pre-commit" (
            echo ⚓ Automating local git hooks ^(pre-commit^)...            "%VENV_PATH%\Scripts\pre-commit.exe" install >nul 2>&1
        )
    )
)

:: Run the script using the venv's python interpreter
"%VENV_PATH%\Scripts\python.exe" "%PYTHON_SCRIPT%" %*
