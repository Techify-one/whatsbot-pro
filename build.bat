@echo off
echo ============================================
echo  WhatsBot - Build Script
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado. Instale Python 3.11+ primeiro.
    pause
    exit /b 1
)

REM Create venv if not exists
if not exist "venv" (
    echo Criando ambiente virtual...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install dependencies
echo Instalando dependencias...
pip install -r requirements.txt

REM Check gowa.exe
if not exist "bin\gowa.exe" (
    echo.
    echo AVISO: bin\gowa.exe nao encontrado!
    echo Baixe o GOWA de: https://github.com/nicnocquee/gowa/releases
    echo Coloque o executavel em bin\gowa.exe
    echo.
    pause
    exit /b 1
)

REM Build
echo.
echo Gerando executavel com PyInstaller...
pyinstaller whatsbot.spec --noconfirm

echo.
echo ============================================
echo  Build completo! Verifique dist\WhatsBot\
echo ============================================
pause
