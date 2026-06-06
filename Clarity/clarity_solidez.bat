@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
chcp 65001 >NUL

set "BASE_DIR=D:\Proyectos\4_BI_Ecom\Clarity"
set "LOGDIR=D:\Proyectos\4_BI_Ecom\Logs"
set "SCRIPT=%BASE_DIR%\clarity_extractor_to_sql.py"

set "PY_EXE=%BASE_DIR%\venv\Scripts\python.exe"
if exist "%PY_EXE%" (
  set "PY=%PY_EXE%"
) else (
  set "PY=python"
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f %%d in ('powershell -NoLogo -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"
set "LOGFILE=%LOGDIR%\clarity_solidez_log_%DOW%.txt"

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"

> "%LOGFILE%" (
  echo ================================================================
  echo [%TS%] INICIO EJECUCION - Clarity Solidez
  echo Base   : %BASE_DIR%
  echo Python : %PY%
  echo Script : %SCRIPT%
  echo ================================================================
)

pushd "%BASE_DIR%"
"%PY%" "%SCRIPT%" >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
popd

for /f "usebackq delims=" %%I in (`powershell -NoLogo -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "TS=%%I"
if %RC% neq 0 (
  echo [%TS%] ERROR - Clarity termino con RC=%RC% >> "%LOGFILE%"
  exit /b %RC%
)

echo [%TS%] OK - Clarity finalizado correctamente >> "%LOGFILE%"
exit /b 0
