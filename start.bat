@echo off
echo Starting WhatsBot (dev mode with hot-reload)...

:: Matar processos anteriores que podem estar pendurados
taskkill /F /IM gowa.exe >nul 2>&1
:: Matar servidor anterior pela window title (arvore inteira: cmd + python + workers)
taskkill /F /T /FI "WINDOWTITLE eq WhatsBot-Server*" >nul 2>&1
:: Aguardar sockets serem liberados
timeout /t 2 /nobreak >nul

if not exist "venv" (
    echo Criando venv...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -q -r requirements.txt

echo.
echo  Hot-reload ativo: alteracoes em server/, agent/, config/, gowa/, db/ reiniciam automaticamente.
echo  Frontend (web/) nao precisa restart - basta dar F5 no browser.
echo.

set NO_COLOR=1

:: Abrir browser apos 3s (apenas no start manual, nao no hot-reload)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8080"

:: Iniciar servidor em janela minimizada (title usado para cleanup no proximo start)
powershell -Command "Start-Process cmd -ArgumentList '/c title WhatsBot-Server && call venv\Scripts\activate.bat && uvicorn server.dev:app --host 0.0.0.0 --port 8080 --reload --reload-dir server --reload-dir agent --reload-dir config --reload-dir gowa --reload-dir db --log-level warning' -WindowStyle Minimized"
