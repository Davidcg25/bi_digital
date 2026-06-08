@echo off
setlocal ENABLEEXTENSIONS
set "PY=D:\Proyectos\4_BI_Ecom\venv\Scripts\python.exe"
set "LOGDIR=D:\Proyectos\4_BI_Ecom\Logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\digest_log_%DOW%.txt"
cd /d "D:\Proyectos\4_BI_Ecom"
echo [%DATE% %TIME%] INICIO digest > "%LOGFILE%"
"%PY%" "Diagnostico\digest.py" >> "%LOGFILE%" 2>&1
echo [%DATE% %TIME%] FIN RC=%ERRORLEVEL% >> "%LOGFILE%"
endlocal
