@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title LGS Egitim Platformu

echo ============================================================
echo    LGS EGITIM PLATFORMU
echo ============================================================
echo.

rem --- Python var mi? ---
set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 (
    set PY=py
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [HATA] Bilgisayarinizda Python bulunamadi.
        echo.
        echo Cozum: https://www.python.org/downloads/ adresinden Python indirin.
        echo Kurulum ekraninda "Add Python to PATH" kutusunu MUTLAKA isaretleyin.
        echo Kurduktan sonra bu dosyaya tekrar cift tiklayin.
        echo.
        pause
        exit /b 1
    )
)

echo [1/3] Gerekli kutuphaneler kontrol ediliyor...
echo       (Ilk calistirmada birkac dakika surebilir, lutfen bekleyin)
echo.
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo [UYARI] Bazi kutuphaneler kurulamadi. Yine de baslatmayi deneyecegiz.
    echo.
)

echo.
echo [2/3] TABLETTEN BAGLANMAK ICIN ADRES:
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do echo        http://%%b:8501
)
echo.
echo        Yukaridaki adreslerden birini tabletin tarayicisina yazin.
echo        Tabletin BU BILGISAYARLA AYNI Wi-Fi agina bagli olmasi gerekir.
echo        Birden fazla adres varsa sirayla deneyin.
echo.
echo        Bu bilgisayarda kullanmak icin: http://localhost:8501
echo.

echo [3/3] Uygulama baslatiliyor...
echo.
echo        ONEMLI: Uygulama calisirken BU PENCEREYI KAPATMAYIN.
echo        Kapatirsaniz tabletten baglanti da kesilir.
echo.
echo ============================================================
echo.

%PY% -m streamlit run app.py

echo.
echo Uygulama durdu.
pause
