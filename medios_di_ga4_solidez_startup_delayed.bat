@echo off
setlocal

REM Solo corre de lunes a viernes. Sabado/domingo sale sin ejecutar GA4.
for /f %%d in ('powershell -NoLogo -NoProfile -Command "(Get-Date).DayOfWeek.value__"') do set "DOW=%%d"
if %DOW% EQU 0 exit /b 0
if %DOW% EQU 6 exit /b 0

REM Espera 5 minutos despues del inicio de sesion para no competir con el arranque.
timeout /t 300 /nobreak >NUL

call "D:\Proyectos\4_BI_Ecom\medios_di_ga4_solidez.bat"
exit /b %ERRORLEVEL%
