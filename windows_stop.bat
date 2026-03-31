@echo off
echo Stopping WhatsBot...

taskkill /F /IM gowa.exe >nul 2>&1

:: Matar processos python do WhatsBot (commandline contendo "whatsbot" ou "server.dev")
:: e processos python orfaos de multiprocessing (pai ja morreu)
powershell -Command ^
  "$procs = Get-CimInstance Win32_Process -Filter \"name='python.exe'\"; " ^
  "$allPids = $procs | ForEach-Object { $_.ProcessId }; " ^
  "foreach ($p in $procs) { " ^
  "  $kill = $false; " ^
  "  if ($p.CommandLine -like '*whatsbot*' -or $p.CommandLine -like '*server.dev*') { $kill = $true } " ^
  "  if ($p.CommandLine -like '*multiprocessing*' -and $p.ParentProcessId -notin $allPids) { $kill = $true } " ^
  "  if ($kill) { taskkill /F /PID $p.ProcessId 2>&1 | Out-Null } " ^
  "}" >nul 2>&1

echo WhatsBot stopped.
timeout /t 2 /nobreak >nul
