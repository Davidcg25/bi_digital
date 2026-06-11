@echo off
setlocal ENABLEEXTENSIONS
chcp 65001 >NUL

REM ==== RUTAS ====
set "BASE_DIR=D:\Proyectos\4_BI_Ecom"
set "ETL_DIR=%BASE_DIR%\Magento_Orders"
set "LOGDIR=%BASE_DIR%\Logs"

REM ==== Python: usa venv si existe; si no, python del sistema ====
set "PY_EXE=%BASE_DIR%\venv\Scripts\python.exe"
if exist "%PY_EXE%" (
  set "PY=%PY_EXE%"
) else (
  set "PY=python"
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM DOW fijo en espanol para rotar logs por dia.
for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\magento_orders_log_%DOW%.txt"

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"

> "%LOGFILE%" (
  echo ================================================================
  echo [%TS%] INICIO EJECUCION - Ordenes Magento ^(API export droplet^)
  echo Base ETL : %ETL_DIR%
  echo Python   : %PY%
  echo ================================================================
)

pushd "%ETL_DIR%"
"%PY%" "%ETL_DIR%\etl_magento_orders.py" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
popd

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"
if %RC% neq 0 (
  echo [%TS%] ERROR - etl_magento_orders termino con RC=%RC% >> "%LOGFILE%"
  exit /b %RC%
)

echo [%TS%] OK - Ordenes Magento finalizado correctamente >> "%LOGFILE%"
exit /b 0
