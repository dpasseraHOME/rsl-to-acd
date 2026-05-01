@echo off

:: ─────────────────────────────────────────────────────────────────────────────
:: Step 1: Check that Python is installed
:: ─────────────────────────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 goto :no_python

:: ─────────────────────────────────────────────────────────────────────────────
:: Step 2: Check Python version is 3.8 or higher
:: ─────────────────────────────────────────────────────────────────────────────
python -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if %errorlevel% neq 0 goto :wrong_version

:: ─────────────────────────────────────────────────────────────────────────────
:: Step 3: Auto-install required libraries (fast no-op when already up to date)
:: ─────────────────────────────────────────────────────────────────────────────
echo Checking required libraries...
python -m pip install acd-tools rich --quiet --upgrade
if %errorlevel% neq 0 goto :pip_failed

:: ─────────────────────────────────────────────────────────────────────────────
:: Step 4: Launch the wizard
:: ─────────────────────────────────────────────────────────────────────────────
echo.
python wizard.py
pause
exit /b 0


:: ─────────────────────────────────────────────────────────────────────────────
:no_python
echo.
echo  =========================================================
echo   Python is not installed.
echo  =========================================================
echo.
echo  To use this tool, you need to install Python first.
echo.
echo  Steps:
echo    1. Your browser will open the Python download page.
echo    2. Click the yellow "Download Python" button.
echo    3. Run the installer.
echo    4. IMPORTANT: On the first screen of the installer,
echo       check the box that says "Add Python to PATH"
echo       before clicking Install.
echo    5. Once installation is finished, close this window
echo       and double-click run.bat again.
echo.
echo  Opening https://www.python.org/downloads/ ...
echo.
start https://www.python.org/downloads/
pause
exit /b 1


:: ─────────────────────────────────────────────────────────────────────────────
:wrong_version
echo.
echo  =========================================================
echo   Python 3.8 or higher is required.
echo  =========================================================
echo.
echo  Your current Python version is too old.
echo  Please download and install the latest version of Python.
echo.
echo  Steps:
echo    1. Your browser will open the Python download page.
echo    2. Download and install the latest version.
echo    3. Check "Add Python to PATH" during installation.
echo    4. Close this window and double-click run.bat again.
echo.
echo  Opening https://www.python.org/downloads/ ...
echo.
start https://www.python.org/downloads/
pause
exit /b 1


:: ─────────────────────────────────────────────────────────────────────────────
:pip_failed
echo.
echo  =========================================================
echo   Could not install required libraries automatically.
echo  =========================================================
echo.
echo  Please try running this command yourself:
echo.
echo    pip install acd-tools rich
echo.
echo  Open Command Prompt, paste the line above, and press Enter.
echo  Then close this window and double-click run.bat again.
echo.
pause
exit /b 1
