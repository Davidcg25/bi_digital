@echo off
setlocal

:: Ruta a la carpeta de logs
set LOGDIR="D:\Proyectos\4_BI_Ecom\Logs"
:: Generar nombre dinámico de log con fecha y hora
set LOGFILE=%LOGDIR%\ventas_solidez_log_%DATE:/=-%_%TIME::=-%.txt

:: Crear carpeta de logs si no existe
if not exist %LOGDIR% mkdir %LOGDIR%

echo 🕒 Inicio: %DATE% %TIME% >> %LOGFILE%

:: Ejecutar primer script
echo 🔄 Ejecutando ventas_solidez-rmh.py... >> %LOGFILE%
cd /d "D:\Proyectos\4_BI_Ecom"
python ventas_solidez-rmh.py >> %LOGFILE% 2>&1

IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Error ejecutando ventas_solidez-rmh.py. Abortando... >> %LOGFILE%
    exit /b %ERRORLEVEL%
)

:: Ejecutar segundo script
echo ✅ Sincronización SQL completada. Ejecutando DI_Solidez_Ventas_Medios.py... >> %LOGFILE%
cd /d "D:\Proyectos\4_BI_Ecom\Vistas_RMH"
python DI_Solidez_Ventas_Medios.py >> %LOGFILE% 2>&1

IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Error ejecutando DI_Solidez_Ventas_Medios.py >> %LOGFILE%
    exit /b %ERRORLEVEL%
)

echo ✅ Todo finalizado correctamente. >> %LOGFILE%
echo 🕒 Fin: %DATE% %TIME% >> %LOGFILE%

endlocal
