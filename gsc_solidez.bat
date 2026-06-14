@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
chcp 65001 >NUL

REM ==== RUTAS ====
set "BASE_DIR=D:\Proyectos\4_BI_Ecom"
set "GSC_DIR=%BASE_DIR%\GSC"
set "LOGDIR=%BASE_DIR%\Logs"

REM ==== Grano MENSUAL (mes cerrado -> gsc_monthly_*). Ventana default ~16 meses. ====
set "GRAIN=monthly"
set "START_YM="
set "END_YM="
set "PROPERTIES="

REM ==== Python: usa venv si existe; si no, python del sistema ====
set "PY_EXE=D:\Proyectos\4_BI_Ecom\venv\Scripts\python.exe"
if exist "%PY_EXE%" (
  set "PY=%PY_EXE%"
) else (
  set "PY=python"
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM DOW fijo en espanol para rotar logs por dia.
for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\gsc_solidez_log_%DOW%.txt"

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"

> "%LOGFILE%" (
  echo ================================================================
  echo [%TS%] INICIO EJECUCION - GSC Solidez ^(mensual^)
  echo Base GSC : %GSC_DIR%
  echo Python   : %PY%
  echo GRAIN    : %GRAIN%
  echo ================================================================
)

pushd "%GSC_DIR%"
"%PY%" "%GSC_DIR%\gsc_extractor_to_sql.py" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
popd

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"
if %RC% neq 0 (
  echo [%TS%] ERROR - GSC mensual termino con RC=%RC% >> "%LOGFILE%"
  exit /b %RC%
)

echo [%TS%] OK - GSC mensual finalizado correctamente >> "%LOGFILE%"
exit /b 0
