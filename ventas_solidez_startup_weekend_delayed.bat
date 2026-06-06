@echo off
setlocal

REM Solo corre sabado/domingo. En lunes-viernes sale sin ejecutar ventas.
for /f %%d in ('powershell -NoLogo -NoProfile -Command "(Get-Date).DayOfWeek.value__"') do set "DOW=%%d"
if not %DOW% EQU 0 if not %DOW% EQU 6 exit /b 0

REM Espera 15 minutos despues del inicio de sesion.
timeout /t 900 /nobreak >NUL

call "D:\Proyectos\4_BI_Ecom\sincronizacion_di_solidez.bat"
exit /b %ERRORLEVEL%
