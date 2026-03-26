@echo off
echo Starting WhatsBot (dev mode with hot-reload)...

:: Matar processos anteriores que podem estar pendurados
taskkill /F /IM gowa.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

if not exist "venv" (
    echo Criando venv...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -q -r requirements.txt

echo.
echo  Hot-reload ativo: alteracoes em server/, agent/, config/ reiniciam automaticamente.
echo  Frontend (web/) nao precisa restart - basta dar F5 no browser.
echo.

set NO_COLOR=1

:: Abrir browser apos 3s (apenas no start manual, nao no hot-reload)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8080"

uvicorn server.dev:app --host 0.0.0.0 --port 8080 --reload --reload-dir server --reload-dir agent --reload-dir config --log-level warning
pause
