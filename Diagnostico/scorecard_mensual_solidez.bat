@echo off
REM ============================================================================
REM scorecard_mensual_solidez.bat - Scorecard del ultimo MES CERRADO.
REM Pensado para Task Scheduler a INICIO DE MES (p.ej. dia 2). Sin --periodo el
REM script toma closed_month() = mes anterior completo. Genera todas las marcas x
REM todos los perfiles. Durante el mes en curso usar scorecard_semanal_solidez.bat.
REM ============================================================================
setlocal ENABLEEXTENSIONS
chcp 65001 >NUL

set "BASE_DIR=D:\Proyectos\4_BI_Ecom"
set "PY=%BASE_DIR%\venv\Scripts\python.exe"
set "LOGDIR=%BASE_DIR%\Logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Log por YYYYMM de la corrida (una al mes).
for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyyMM')"`) do set "YM=%%I"
set "LOGFILE=%LOGDIR%\scorecard_mensual_log_%YM%.txt"

cd /d "%BASE_DIR%"
echo [%DATE% %TIME%] INICIO scorecard MENSUAL (mes cerrado) > "%LOGFILE%"
"%PY%" "Diagnostico\build_scorecard.py" >> "%LOGFILE%" 2>&1
echo [%DATE% %TIME%] FIN RC=%ERRORLEVEL% >> "%LOGFILE%"
endlocal
