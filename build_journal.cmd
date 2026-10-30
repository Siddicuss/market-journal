@echo off
rem Builds the app (dist\MarketJournal\MarketJournal.exe) and its installer (dist\MarketJournal-Setup.exe).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating the journal's Python environment...
  py -3.14 -m venv .venv || goto :fail
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail
echo Running tests...
.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py" || goto :fail
.venv\Scripts\python.exe make_icon.py || goto :fail
echo Building MarketJournal.exe...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed --name MarketJournal ^
  --icon "%~dp0build\icon.ico" --add-data "%~dp0web;web" ^
  --distpath "%~dp0dist" --workpath "%~dp0build\work" --specpath "%~dp0build" main.py || goto :fail
echo Packing the installer...
.venv\Scripts\python.exe -c "import shutil; shutil.make_archive(r'build\payload', 'zip', r'dist\MarketJournal')" || goto :fail
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --log-level WARN --onefile --windowed --name MarketJournal-Setup ^
  --icon "%~dp0build\icon.ico" --add-data "%~dp0build\payload.zip;." --add-data "%~dp0build\icon.ico;." ^
  --distpath "%~dp0dist" --workpath "%~dp0build\work-setup" --specpath "%~dp0build" installer.py || goto :fail
echo.
echo Built: %~dp0dist\MarketJournal-Setup.exe
echo Share that one file. Running it installs Market Journal with Start menu and Desktop shortcuts.
exit /b 0
:fail
echo.
echo Build failed.
exit /b 1
