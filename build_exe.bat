@echo off
title Compilando Detector de Duplicados de Audio
echo ========================================================
echo   COMPILANDO APLICACION COMERCIAL (.EXE AUTONOMO)
echo ========================================================
echo.

REM 1. Instalar dependencias necesarias incluyendo PyInstaller
echo [1/3] Verificando dependencias...
pip install -r requirements.txt
pip install pyinstaller

REM 2. Limpiar compilaciones anteriores
echo.
echo [2/3] Limpiando carpetas temporales de compilacion...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

REM 3. Ejecutar PyInstaller con la configuracion .spec
echo.
echo [3/3] Empaquetando aplicacion con PyInstaller...
pyinstaller --clean build_installer.spec

echo.
if exist "dist\AudioDuplicateDetector.exe" (
    echo ========================================================
    echo   EXITO: Aplicacion compilada correctamente!
    echo   Archivo generado: dist\AudioDuplicateDetector.exe
    echo   Listo para distribuir y ejecutar en otras PCs.
    echo ========================================================
) else (
    echo [ERROR] Hubo un problema durante la compilacion.
)
pause
