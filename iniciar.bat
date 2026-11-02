@echo off
rem Inicia o Captura IA (sem janela de console)
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0
