@echo off
setlocal EnableDelayedExpansion

set "ENV_NAME=smart-inventory"
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where conda >nul 2>nul
if errorlevel 1 (
    echo [ERROR] conda not found in PATH. Please install Miniconda/Anaconda first.
    exit /b 1
)

for /f "tokens=*" %%i in ('conda info --base') do set "CONDA_BASE=%%i"
call "%CONDA_BASE%\Scripts\activate.bat"

call conda env list | findstr /B /C:"%ENV_NAME% " >nul
if errorlevel 1 (
    echo [INFO] Creating conda environment "%ENV_NAME%" from environment.yml ...
    call conda env create -f "%SCRIPT_DIR%environment.yml"
    if errorlevel 1 (
        echo [ERROR] Failed to create conda environment.
        exit /b 1
    )
) else (
    echo [INFO] Conda environment "%ENV_NAME%" already exists.
)

call conda activate %ENV_NAME%
if errorlevel 1 (
    echo [ERROR] Failed to activate conda environment "%ENV_NAME%".
    exit /b 1
)

echo [INFO] Launching Streamlit app ...
streamlit run "%SCRIPT_DIR%src\app.py"

endlocal
