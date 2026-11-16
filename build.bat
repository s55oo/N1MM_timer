@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  N1MM_timer - hardened Windows build
rem    build.bat            build with a PyInstaller bootloader compiled from source
rem    build.bat prebuilt   use PyInstaller's prebuilt bootloader (no C compiler needed,
rem                         but more likely to be flagged by antivirus heuristics)
rem
rem  Needs: Python 3.10+ (python.org, with tkinter) and, for the default mode,
rem         "Build Tools for Visual Studio" with "Desktop development with C++".
rem  Optional code signing: set SIGN_PFX=path\cert.pfx and SIGN_PFX_PASSWORD=...
rem  Output: dist\N1MM_timer-<version>-win64.zip + dist\SHA256SUMS.txt
rem ===========================================================================
cd /d "%~dp0"
set "PYI_VERSION=6.22.3"
set "VENV=.venv-build"

where python >nul 2>nul || (echo Python not found in PATH & exit /b 1)
if not exist "%VENV%\Scripts\python.exe" (
  echo [1/7] creating build virtualenv %VENV%
  python -m venv "%VENV%" || exit /b 1
)
set "PY=%VENV%\Scripts\python.exe"
"%PY%" -m pip install --quiet --upgrade pip || exit /b 1

echo [2/7] installing PyInstaller %PYI_VERSION%
if /i "%~1"=="prebuilt" (
  "%PY%" -m pip install --quiet "pyinstaller==%PYI_VERSION%" || exit /b 1
  echo   WARNING: prebuilt bootloader - antivirus false positives are more likely
) else (
  if not exist "%VENV%\bootloader-%PYI_VERSION%.ok" (
    "%PY%" -m pip install --quiet setuptools wheel || exit /b 1
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_bootloader.ps1 -Python "%PY%" -Version %PYI_VERSION%
    if errorlevel 1 (
      echo.
      echo   Compiling the PyInstaller bootloader failed. Install "Build Tools for Visual Studio"
      echo   with the "Desktop development with C++" workload, or run:  build.bat prebuilt
      exit /b 1
    )
    echo ok> "%VENV%\bootloader-%PYI_VERSION%.ok"
  )
)

echo [3/7] unit tests
"%PY%" -m unittest discover -s tests -t . || exit /b 1

echo [4/7] version resource
"%PY%" packaging\make_version_file.py || exit /b 1
"%PY%" packaging\make_version_file.py --print-version > "%TEMP%\n1mm_timer_ver.txt" || exit /b 1
set /p VER=<"%TEMP%\n1mm_timer_ver.txt"

echo [5/7] PyInstaller (one-folder, no UPX)
"%PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\N1MM_timer.spec || exit /b 1

if defined SIGN_PFX (
  echo [6/7] Authenticode signing
  where signtool >nul 2>nul || (echo   signtool.exe not in PATH - use a "Developer Command Prompt" & exit /b 1)
  signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f "%SIGN_PFX%" /p "%SIGN_PFX_PASSWORD%" dist\N1MM_timer\N1MM_timer.exe dist\N1MM_timer\N1MM_timer_console.exe || exit /b 1
  signtool verify /pa dist\N1MM_timer\N1MM_timer.exe || exit /b 1
) else (
  echo [6/7] not signed ^(set SIGN_PFX to sign^)
)

echo [7/7] Microsoft Defender scan, zip and SHA-256
set "MPCMD=%ProgramFiles%\Windows Defender\MpCmdRun.exe"
if exist "%MPCMD%" (
  "%MPCMD%" -Scan -ScanType 3 -File "%CD%\dist\N1MM_timer" -DisableRemediation
  if errorlevel 2 (echo   DEFENDER REPORTED A THREAT - do not distribute this build & exit /b 2)
) else (
  echo   MpCmdRun.exe not found - skipped Defender scan
)
set "ZIP=dist\N1MM_timer-%VER%-win64.zip"
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\package.ps1 -Version "%VER%" || exit /b 1
type dist\SHA256SUMS.txt
echo.
echo Done: %ZIP%
endlocal
