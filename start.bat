@echo off
:: Se chamado com --server, executar o servidor direto
if "%1"=="--server" goto :server

:: Matar processos anteriores que podem estar pendurados
taskkill /F /IM gowa.exe >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq WhatsBot-Server*" >nul 2>&1

:: Abrir browser apos 5s
start "" cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8080"

:: Relancar este script minimizado no modo servidor e fechar este terminal
powershell -Command "Start-Process cmd -ArgumentList '/c title WhatsBot-Server && call start.bat --server' -WindowStyle Hidden"
exit

:server
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate.bat
pip install -q -r requirements.txt
set NO_COLOR=1
uvicorn server.dev:app --host 0.0.0.0 --port 8080 --reload --reload-dir server --reload-dir agent --reload-dir config --reload-dir gowa --reload-dir db --log-level warning
