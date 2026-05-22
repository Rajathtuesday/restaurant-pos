@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  Rasova Print Agent Installer
::  One-click setup for Windows laptops in a QSR outlet.
::  No admin rights required (uses %APPDATA%).
:: ============================================================

echo.
echo  =========================================================
echo   Rasova Print Agent Installer
echo   Setting up the billing printer bridge...
echo  =========================================================
echo.

:: ── Step 1: Check for Python ────────────────────────────────
python --version >nul 2>&1
if %errorlevel% == 0 (
    echo  [OK] Python found.
    goto :python_ready
)

echo  [!] Python not found in PATH. Attempting install via winget...
echo.

:: Check if winget is available
winget --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] winget is not available on this system.
    echo.
    echo  Please install Python manually:
    echo    1. Open your browser and go to: https://www.python.org/downloads/
    echo    2. Download Python 3.11 or newer
    echo    3. Run the installer -- CHECK "Add Python to PATH"
    echo    4. Re-run this installer
    echo.
    pause
    exit /b 1
)

:: Install Python via winget
winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
if %errorlevel% neq 0 (
    echo  [WARNING] winget install returned a non-zero exit code.
    echo  This may mean Python was already installed or requires a reboot.
)

:: Refresh PATH from registry so this session can see the new Python
echo  Refreshing PATH from registry...
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul') do (
    set "USER_PATH=%%B"
)
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do (
    set "SYS_PATH=%%B"
)
set "PATH=%SYS_PATH%;%USER_PATH%"

:: Try again
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Python still not found after install attempt.
    echo  Please reboot your computer and re-run this installer,
    echo  OR install Python manually from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo  [OK] Python installed successfully via winget.

:python_ready
echo.

:: ── Step 2: Create %APPDATA%\Rasova directory ───────────────
echo  [2/5] Creating Rasova data folder...
if not exist "%APPDATA%\Rasova" (
    mkdir "%APPDATA%\Rasova"
    echo  [OK] Created: %APPDATA%\Rasova
) else (
    echo  [OK] Folder already exists: %APPDATA%\Rasova
)
echo.

:: ── Step 3: Download rasova_agent.py from GitHub ────────────
echo  [3/5] Downloading Rasova Print Agent from GitHub...
set "AGENT_URL=https://raw.githubusercontent.com/Rajathtuesday/restaurant-pos/qsr/rasova_agent.py"
set "AGENT_PATH=%APPDATA%\Rasova\rasova_agent.py"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { Invoke-WebRequest -Uri '%AGENT_URL%' -OutFile '%AGENT_PATH%' -UseBasicParsing -ErrorAction Stop; Write-Host '  [OK] Downloaded rasova_agent.py' } catch { Write-Host ('  [ERROR] Download failed: ' + $_.Exception.Message); exit 1 }"

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Could not download rasova_agent.py.
    echo  Check your internet connection and try again.
    echo  URL: %AGENT_URL%
    echo.
    pause
    exit /b 1
)
echo.

:: ── Step 4: Run --install (pip deps + VBS startup entry) ────
echo  [4/5] Installing dependencies and setting up auto-start...
echo  (This may take a minute while pip downloads packages)
echo.
python "%AGENT_PATH%" --install
if %errorlevel% neq 0 (
    echo.
    echo  [WARNING] --install step returned an error.
    echo  The agent may still work -- check output above.
)
echo.

:: ── Step 5: Done ─────────────────────────────────────────────
echo  [5/5] Setup complete!
echo.
echo  =========================================================
echo   Rasova Print Agent is now installed.
echo.
echo   - It will auto-start silently at every Windows login.
echo   - To test right now: open a new terminal and run:
echo       python "%APPDATA%\Rasova\rasova_agent.py"
echo.
echo   - Log file: %APPDATA%\Rasova\agent.log
echo   - Config:   %APPDATA%\Rasova\agent_config.json
echo.
echo   To uninstall auto-start later:
echo     python "%APPDATA%\Rasova\rasova_agent.py" --uninstall
echo  =========================================================
echo.

pause
endlocal
