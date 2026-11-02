@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "repository_root=%~dp0"
set "directory_pushed="
if not defined LOCALAPPDATA (
    echo Error: LOCALAPPDATA is not defined. >&2
    goto failed
)
if "%LOCALAPPDATA:~0,2%"=="\\" (
    echo Error: LOCALAPPDATA must be on a local Windows drive. >&2
    goto failed
)

set "build_root=%LOCALAPPDATA%\PoissonOddsDemo\project"
set "venv_directory=%LOCALAPPDATA%\PoissonOddsDemo\venv-py313"
set "venv_python=%venv_directory%\Scripts\python.exe"

if not exist "%build_root%\assets" mkdir "%build_root%\assets"
if not exist "%build_root%\assets" (
    echo Error: Unable to create the Windows-local build directory. >&2
    goto failed
)
pushd "%build_root%"
if errorlevel 1 (
    echo Error: Unable to access the Windows-local build directory. >&2
    goto failed
)
set "directory_pushed=1"

echo Copying project files to "%build_root%"...
for %%F in (main.py pyproject.toml LICENSE) do (
    copy /y "%repository_root%%%F" "%%F" >nul
    if errorlevel 1 goto copy_failed
)
copy /y "%repository_root%assets\nhl.ico" "assets\nhl.ico" >nul
if errorlevel 1 goto copy_failed

rem Mirror only app source, never the repository or its virtual environments.
robocopy "%repository_root%app" "app" *.py /MIR /XD __pycache__ /R:2 /W:1 /NJH /NJS /NFL /NDL
rem Robocopy exit codes below 8 indicate success, including copied files.
if errorlevel 8 goto copy_failed

if exist "%venv_python%" goto install
if exist "%venv_directory%" (
    echo Error: The Windows-local virtual environment is incomplete. It was not deleted. >&2
    echo Location: "%venv_directory%" >&2
    goto failed
)
echo Creating Windows-local virtual environment with Python 3.13...
py -3.13 -m venv "%venv_directory%"
if errorlevel 1 (
    echo Error: Unable to create the environment. Windows Python 3.13 with venv support is required. >&2
    goto failed
)

:install
"%venv_python%" -c "import sys; raise SystemExit(0 if sys.platform == 'win32' and sys.version_info[:2] == (3, 13) else 1)"
if errorlevel 1 (
    echo Error: The Windows-local environment needs a working Windows Python 3.13 interpreter. >&2
    goto failed
)
echo Using virtual environment "%venv_directory%".
echo Installing application and build dependencies...
"%venv_python%" -m pip install ".[build]"
if errorlevel 1 (
    echo Error: Application or build dependency installation failed. >&2
    goto failed
)

echo Building Windows console executable on the local Windows drive...
"%venv_python%" -m PyInstaller --noconfirm --clean --onefile --console ^
    --name poisson-odds-demo --icon assets/nhl.ico ^
    --collect-data fake_useragent main.py
if errorlevel 1 (
    echo Error: PyInstaller build failed. >&2
    goto failed
)
if not exist "dist\poisson-odds-demo.exe" (
    echo Error: Expected executable was not created. >&2
    goto failed
)

echo Copying executable back to the repository...
if not exist "%repository_root%dist" mkdir "%repository_root%dist"
if not exist "%repository_root%dist" goto copy_failed
copy /y "dist\poisson-odds-demo.exe" "%repository_root%dist\poisson-odds-demo.exe" >nul
if errorlevel 1 goto copy_failed

echo.
echo Build complete: "%repository_root%dist\poisson-odds-demo.exe"
popd
pause
exit /b 0

:copy_failed
echo Error: Unable to copy project files or the executable. Check the source and destination paths. >&2

:failed
echo Build failed. >&2
if defined directory_pushed popd
pause
exit /b 1
