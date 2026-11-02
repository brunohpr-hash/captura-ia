@echo off
chcp 65001 >nul
title Captura IA — Instalador
echo ============================================
echo   CAPTURA IA — instalacao no Windows
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo Instale em https://www.python.org/downloads/
    echo IMPORTANTE: marque "Add Python to PATH" na instalacao.
    pause
    exit /b 1
)

echo [1/3] Criando ambiente virtual...
if not exist "%~dp0venv" python -m venv "%~dp0venv"

echo [2/3] Instalando dependencias...
"%~dp0venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install --quiet -r "%~dp0requirements.txt"

echo [3/3] Criando atalhos (inicializacao + area de trabalho)...
set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CapturaIA.bat
echo @echo off > "%SHORTCUT%"
echo start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py" >> "%SHORTCUT%"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Captura IA.lnk'); $s.TargetPath = '%~dp0venv\Scripts\pythonw.exe'; $s.Arguments = '\"%~dp0main.py\"'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0captura.ico'; $s.Save()"

echo.
echo ============================================
echo   Instalacao concluida!
echo   O app abrira agora — cole sua chave Groq
echo   (pode ser a MESMA do Ditado IA).
echo   Atalho padrao: ALT+C
echo ============================================
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0
