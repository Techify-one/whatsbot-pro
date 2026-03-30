@echo off
echo Stopping WhatsBot...

taskkill /F /IM gowa.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080 " ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

echo WhatsBot stopped.
timeout /t 2 /nobreak >nul
