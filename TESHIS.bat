@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title LGS Teshis
if exist "__pycache__" rd /s /q "__pycache__" >nul 2>&1
echo ============================================================
echo    KITAP TESHIS ARACI
echo ============================================================
echo.
echo Bu arac, secilen PDF i sayfa sayfa inceleyip programin ne
echo gordugunu "teshis_raporu.txt" dosyasina yazar.
echo Buyuk kitaplarda birkac dakika surebilir.
echo.
set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 set PY=py
%PY% teshis.py %1
