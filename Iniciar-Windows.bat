@echo off
chcp 65001 >nul
title Monitor de IA
cd /d "%~dp0"

echo Procurando o Python...

where py >nul 2>nul
if %errorlevel%==0 (
    py app.py
    goto fim
)

where python >nul 2>nul
if %errorlevel%==0 (
    python app.py
    goto fim
)

echo.
echo ============================================================
echo  O Python nao foi encontrado neste computador.
echo  Instale em: https://www.python.org/downloads/
echo  (Marque a opcao "Add Python to PATH" na instalacao)
echo ============================================================
echo.
pause
goto :eof

:fim
echo.
echo O monitor foi encerrado.
pause
