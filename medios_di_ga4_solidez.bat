@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
chcp 65001 >NUL

REM ==== RUTAS ====
set "BASE_DIR=D:\Proyectos\4_BI_Ecom"
set "GA4_DIR=%BASE_DIR%\GA4"
set "LOGDIR=%BASE_DIR%\Logs"

REM ==== Configuracion GA4 para ejecucion recurrente ====
REM 62 dias cubre hasta 2 meses completos sin volver a traer 12 meses.
set "LOOKBACK_DAYS=62"
set "START_DATE="
set "END_DATE="
REM Deja PROPERTY_IDS_TO_RUN vacio para correr todas las properties definidas en ga4_config.py.
set "PROPERTY_IDS_TO_RUN="

REM ==== Python: usa venv si existe; si no, python del sistema ====
set "PY_EXE=%GA4_DIR%\venv\Scripts\python.exe"
if exist "%PY_EXE%" (
  set "PY=%PY_EXE%"
) else (
  set "PY=python"
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM DOW fijo en español para rotar logs por dia.
for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\medios_ga4_solidez_log_%DOW%.txt"

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"

> "%LOGFILE%" (
  echo ================================================================
  echo [%TS%] INICIO EJECUCION - GA4 Solidez
  echo Base GA4      : %GA4_DIR%
  echo Python        : %PY%
  echo LOOKBACK_DAYS : %LOOKBACK_DAYS%
  echo ================================================================
)

pushd "%GA4_DIR%"
"%PY%" "%GA4_DIR%\ga4_extractor_to_sql.py" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% neq 0 (
  popd
  goto :finish
)

echo. >> "%LOGFILE%"
echo [%TS%] INICIO - GA4 Ecommerce -> Google Sheets >> "%LOGFILE%"
"%PY%" "%GA4_DIR%\ga4_ecommerce_to_sheets.py" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
popd

:finish
for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"
if %RC% neq 0 (
  echo [%TS%] ERROR - GA4 termino con RC=%RC% >> "%LOGFILE%"
  exit /b %RC%
)

echo [%TS%] OK - GA4 finalizado correctamente >> "%LOGFILE%"
exit /b 0

