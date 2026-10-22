@echo off
setlocal
cd /d "%~dp0"
if not exist .venv python -m venv .venv || goto :err
set PY=.venv\Scripts\python.exe
%PY% -m pip install -q --upgrade pip
%PY% -m pip install -q -r requirements.txt pyinstaller || goto :err
%PY% make_icon.py || goto :err
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir --noupx --name XDM1041_Studio --icon icon.ico ^
  --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore ^
  xdm1041_studio.py || goto :err
set ISCC=
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%P
if defined ISCC (
  %ISCC% /Q installer.iss || goto :err
  echo Installateur : installer\XDM1041_Studio_Setup_2.0.exe
) else (
  echo Inno Setup absent : installateur non genere ^(winget install JRSoftware.InnoSetup^)
)
echo Application : dist\XDM1041_Studio\XDM1041_Studio.exe
exit /b 0
:err
echo ECHEC
exit /b 1
