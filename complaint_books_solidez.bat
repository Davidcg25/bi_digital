@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
chcp 65001 >NUL

REM ==== RUTAS ====
set "BASE_API=D:\Programs\1. Apps\7. Digital Impact\4. BI"
set "BASE_PUSH=D:\Programs\1. Apps\7. Digital Impact\4. BI\Vistas_RMH"
set "LOGDIR=%BASE_API%\logs"

REM ==== VENV opcional ====
set "VENV_ACT=%BASE_API%\venv\Scripts\activate.bat"
set "PY_EXE=%BASE_API%\venv\Scripts\python.exe"
set "PY_FALLBACK=python"

REM ==== SCRIPTS ====
set "SCRIPT_API=complaint_books-extract.py"
set "SCRIPT_SHEETS=push_complaints_to_sheets.py"

REM ==== Credenciales Google ====
set "GOOGLE_APPLICATION_CREDENTIALS=%BASE_PUSH%\di-auth-gsheets.json"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM ==== DOW (lun, mar, mie, jue, vie, sab, dom) ====
for /f %%d in ('powershell -NoProfile -Command "$d=(Get-Date).DayOfWeek; switch($d){'Monday'{'lun'}'Tuesday'{'mar'}'Wednesday'{'mie'}'Thursday'{'jue'}'Friday'{'vie'}'Saturday'{'sab'}'Sunday'{'dom'}}"') do set "DOW=%%d"

REM Archivo de log único por día:
set "LOGFILE=%LOGDIR%\libro_reclamos_log_%DOW%.txt"

REM ==== Timestamp helper ====
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd HH:mm:ss\")"') do set "TS=%%I"

REM ==== Elegir Python ====
if exist "%VENV_ACT%" (
  call "%VENV_ACT%"
  if exist "%PY_EXE%" (
    set "PY=%PY_EXE%"
  ) else (
    set "PY=%PY_FALLBACK%"
  )
) else (
  set "PY=%PY_FALLBACK%"
)

REM ==== CABECERA (sobrescribe al inicio del día) ====
REM Si quieres APPEND permanente, cambia > por >> en la línea de abajo.
> "%LOGFILE%" (
  echo ================================================================
  echo [%TS%] INICIO EJECUCION - Libro de Reclamos (DOW=%DOW%)
  echo Base API  : %BASE_API%
  echo Base PUSH : %BASE_PUSH%
  echo Py       : %PY%
  echo GCreds   : %GOOGLE_APPLICATION_CREDENTIALS%
  echo ================================================================
)

call :run_step "Paso 1 - API -> SQL" "%BASE_API%" "%PY%" "%BASE_API%\%SCRIPT_API%" "%LOGFILE%" || goto :fail

call :run_step "Paso 2 - SQL -> Sheets" "%BASE_PUSH%" "%PY%" "%BASE_PUSH%\%SCRIPT_SHEETS%" "%LOGFILE%" || goto :fail

echo [%date% %time%] OK - Proceso completado >> "%LOGFILE%"
exit /b 0

:run_step
REM %1 = titulo, %2 = workdir, %3 = py, %4 = script, %5 = logfile
set "TITLE=%~1"
set "WD=%~2"
set "PYTH=%~3"
set "SCRIPT=%~4"
set "LOG=%~5"

pushd "%WD%"
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd HH:mm:ss\")"') do set "NOW=%%I"
echo --------------------------------------------------------------- >> "%LOG%"
echo [%NOW%] %TITLE% - START (WD=%CD%) >> "%LOG%"

REM Redirigimos stdout+stderr al log único
"%PYTH%" "%SCRIPT%"  >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd HH:mm:ss\")"') do set "NOW=%%I"
if %RC% neq 0 (
  echo [%NOW%] %TITLE% - ERROR (RC=%RC%) >> "%LOG%"
  popd
  exit /b %RC%
) else (
  echo [%NOW%] %TITLE% - OK >> "%LOG%"
)
popd
exit /b 0

:fail
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd HH:mm:ss\")"') do set "NOW=%%I"
echo [%NOW%] PROCESO TERMINADO CON ERRORES >> "%LOGFILE%"
exit /b 1
