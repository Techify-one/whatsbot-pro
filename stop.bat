@echo off
echo Stopping WhatsBot...

taskkill /F /IM gowa.exe >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq WhatsBot-Server*" >nul 2>&1

echo WhatsBot stopped.
timeout /t 2 /nobreak >nul
