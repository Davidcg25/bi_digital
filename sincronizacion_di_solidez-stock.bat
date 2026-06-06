@echo off
setlocal ENABLEDELAYEDEXPANSION
:: UTF-8 en la consola para que los logs no rompan con caracteres especiales
chcp 65001 > nul

:: ================================
:: Configuración de rutas
:: ================================
set "BI_DIR=D:\Proyectos\4_BI_Ecom"
set "VISTAS_DIR=D:\Proyectos\4_BI_Ecom\Vistas_RMH"
set "LOGDIR=D:\Proyectos\4_BI_Ecom\Logs"

:: ================================
:: Nombre del día de la semana (ej: lun, mar, mie, jue, vie, sab, dom)
:: ================================
for /f "tokens=1" %%a in ('powershell -NoLogo -NoProfile -Command "(Get-Date).ToString(\"ddd\")"') do set DOW=%%a

:: ================================
:: Preparar logs
:: ================================
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOGFILE=%LOGDIR%\stock_solidez_log_%DOW%.txt"

echo Inicio: %DATE% %TIME% >> "%LOGFILE%"

:: ================================
:: 1) Ejecutar extracción/carga a SQL (stock)
:: ================================
echo  Ejecutando stock_solidez-rmh.py... >> "%LOGFILE%"
cd /d "%BI_DIR%"
python stock_solidez-rmh.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Error ejecutando stock_solidez-rmh.py. Abortando... >> "%LOGFILE%"
    echo Fin con error: %DATE% %TIME% >> "%LOGFILE%"
    exit /b 1
)

:: ================================
:: 2) Sincronizar vistas a Google Sheets
:: ================================
echo  SQL actualizado. Ejecutando DI_Solidez_Stock-rmh.py... >> "%LOGFILE%"
cd /d "%VISTAS_DIR%"
python DI_Solidez_Stock-rmh.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Error ejecutando DI_Solidez_Stock-rmh.py >> "%LOGFILE%"
    echo Fin con error: %DATE% %TIME% >> "%LOGFILE%"
    exit /b 1
)

echo Todo finalizado correctamente. >> "%LOGFILE%"
echo Fin: %DATE% %TIME% >> "%LOGFILE%"

endlocal