@echo off
setlocal

REM ============================================
REM  Ejecución automática: magento_reglas_precio.py
REM ============================================

set PYTHON_EXE=C:\Users\david\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe
set SCRIPT_PATH=D:\Programs\1. Apps\7. Digital Impact\4. BI\Promociones Magento\magento_reglas_precio.py
set LOG_FILE=D:\Programs\1. Apps\7. Digital Impact\4. BI\Logs\magento_reglas_precio.log

echo =========================================================== >> "%LOG_FILE%"
echo [INICIO] %date% %time% >> "%LOG_FILE%"

echo Python usado: >> "%LOG_FILE%"
"%PYTHON_EXE%" -c "import sys; print(sys.executable)" >> "%LOG_FILE%" 2>&1

cd /d "D:\Programs\1. Apps\7. Digital Impact\4. BI\Promociones Magento"

"%PYTHON_EXE%" "%SCRIPT_PATH%" >> "%LOG_FILE%" 2>&1

echo [FIN] %date% %time% >> "%LOG_FILE%"
echo =========================================================== >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

endlocal
