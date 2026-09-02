@echo off
REM Build a single portable wardogs-calc.exe into dist\.
REM Kept ASCII-only on purpose: cmd.exe runs .bat files in the OEM codepage,
REM and UTF-8 text here corrupts the "^" line continuations below.
setlocal
cd /d "%~dp0"

echo [1/3] Installing dependencies...
py -3 -m pip install --disable-pip-version-check -r requirements.txt || goto :error

echo [2/3] Optional DXGI capture (safe to skip)...
set "DXCAM_FLAG="
py -3 -m pip install --disable-pip-version-check dxcam
if errorlevel 1 (
  echo     dxcam not installed - building without exclusive-fullscreen capture.
) else (
  set "DXCAM_FLAG=--hidden-import dxcam"
)

echo [3/3] Building...
py -3 -m PyInstaller ^
  --noconfirm --clean --onefile --noconsole ^
  --name wardogs-calc ^
  --paths src ^
  --icon src/wardogs_calc/icon.ico ^
  --add-data "src/wardogs_calc/icon.ico;wardogs_calc" ^
  --add-data "src/wardogs_calc/firing_tables.json;wardogs_calc" ^
  --add-data "src/wardogs_calc/vision/templates;wardogs_calc/vision/templates" ^
  --collect-submodules mss ^
  %DXCAM_FLAG% ^
  main.py || goto :error

echo.
REM Running the exe from dist leaves runtime files behind; a stale
REM glyphs.json next to it would be loaded as user-trained templates.
del /q dist\config.json dist\glyphs.json 2>nul
rmdir /s /q dist\debug 2>nul
copy /y LICENSE dist\LICENSE >nul

echo Done: dist\wardogs-calc.exe
echo Copy the exe anywhere - config.json is created next to it.
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
