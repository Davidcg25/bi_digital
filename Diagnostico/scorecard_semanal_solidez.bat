@echo off
REM ============================================================================
REM scorecard_semanal_solidez.bat - Scorecard del MES EN CURSO (data parcial).
REM Task Scheduler SEMANAL durante el mes presente. Dos pasos:
REM   1) GA4 extractor month-to-date: fuerza el mes en curso (START_DATE=dia 1,
REM      END_DATE=hoy) y carga SOLO los reportes mensuales que consume el
REM      scorecard en ga4_monthly_* (campanas/funnel/devices/canales/items/
REM      busqueda). NO toca las tablas 12m/range ni los meses ya cerrados
REM      (delete/insert por year_month = solo el mes actual).
REM   2) build_scorecard --periodo actual: todas las marcas x todos los perfiles.
REM Para el mes CERRADO de inicio de mes usar scorecard_mensual_solidez.bat.
REM ============================================================================
setlocal ENABLEEXTENSIONS
chcp 65001 >NUL

set "BASE_DIR=D:\Proyectos\4_BI_Ecom"
set "GA4_DIR=%BASE_DIR%\GA4"
set "PY=%BASE_DIR%\venv\Scripts\python.exe"
set "LOGDIR=%BASE_DIR%\Logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Log rotado por dia de la semana (es).
for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\scorecard_semanal_log_%DOW%.txt"

REM Ventana del mes en curso: dia 1 -> hoy. Fija START_DATE/END_DATE => el
REM extractor activa RUN_MONTHLY y recarga SOLO el mes actual por year_month.
for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM')+'-01'"`) do set "START_DATE=%%I"
for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd')"`) do set "END_DATE=%%I"
REM Solo reportes MENSUALES que usa el scorecard (evita clobber de 12m/range/daily).
set "REPORT_NAMES_TO_RUN=monthly_core,monthly_rates,monthly_channels,monthly_devices,monthly_campaigns,items_monthly,search_terms_monthly"

cd /d "%BASE_DIR%"
echo [%DATE% %TIME%] INICIO scorecard SEMANAL (mes en curso %START_DATE%..%END_DATE%) > "%LOGFILE%"

echo [%DATE% %TIME%] Paso 1/2: GA4 extractor month-to-date >> "%LOGFILE%"
pushd "%GA4_DIR%"
"%PY%" "%GA4_DIR%\ga4_extractor_to_sql.py" >> "%LOGFILE%" 2>&1
set "RC_GA4=%ERRORLEVEL%"
popd
echo [%DATE% %TIME%] GA4 extractor RC=%RC_GA4% >> "%LOGFILE%"

REM Limpia la ventana forzada (el scorecard no la lee, pero se deja limpio).
set "START_DATE="
set "END_DATE="
set "REPORT_NAMES_TO_RUN="

REM Se corre el scorecard aunque GA4 falle (las secciones RMH no dependen de GA4).
echo [%DATE% %TIME%] Paso 2/2: build_scorecard --periodo actual >> "%LOGFILE%"
"%PY%" "Diagnostico\build_scorecard.py" --periodo actual >> "%LOGFILE%" 2>&1
echo [%DATE% %TIME%] FIN scorecard RC=%ERRORLEVEL% (GA4 RC=%RC_GA4%) >> "%LOGFILE%"
endlocal
