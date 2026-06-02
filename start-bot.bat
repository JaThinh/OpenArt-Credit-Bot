@echo off
setlocal
title OpenArt and Outlook Automation Tools
echo =======================================================
echo   OpenArt ^& Outlook Register Auto Bot Toolkit
echo =======================================================
echo.
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [!] Khong tim thay Python trong PATH. Vui long cai Python va thu lai.
        pause
        exit /b 1
    )
    echo [0/3] Tao moi moi truong ao .venv...
    python -m venv .venv
)
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo [1/3] Kiem tra cac thu vien Python...
"%PYTHON_EXE%" -B -c "import tempmail, customtkinter, playwright, camoufox, requests, faker, httpx" >nul 2>nul
if errorlevel 1 (
    echo [!] Phat hien thieu thu vien Python. Dang cai dat tu requirements.txt...
    "%PYTHON_EXE%" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo [!] Loi khi cai dat requirements.txt. Dang thu lai goi can thiet...
        "%PYTHON_EXE%" -m pip install temp-mail customtkinter playwright camoufox Faker requests httpx
        if errorlevel 1 (
            echo [!] Khong the cai dat thu vien Python. Vui long kiem tra mang/Python/pip.
            pause
            exit /b 1
        )
    )
    echo [OK] Da cai dat xong thu vien Python.
) else (
    echo [OK] Thu vien Python da san sang.
)

echo [2/3] Kiem tra va tai ve trinh duyet Playwright...
"%PYTHON_EXE%" -m playwright install firefox chromium
if errorlevel 1 (
    echo [!] Loi khi cai trinh duyet Playwright. Ban co the can chay file nay bang quyen Administrator hoac kiem tra mang.
    pause
    exit /b 1
)

echo [3/3] Tai nguyen trinh duyet san sang.
echo.
:MENU
cls
echo =======================================================
echo   CHON CONG CU BAN MUON KHOI CHAY:
echo =======================================================
echo   [1] Khoi chay OpenArt Auto Reg ^& Claim Credits Bot ^(Doc lap^)
echo   [2] Khoi chay Outlook Auto Register Bot ^(GUI Edition - Doc lap^)
echo   [3] Thoat
echo =======================================================
set /p choice="Nhap lua chon cua ban (1-3): "

if "%choice%"=="1" (
    echo.
    echo Dang khoi chay OpenArt Bot...
    echo [*] Kiem tra va tai ve tai nguyen Camoufox...
    "%PYTHON_EXE%" -m camoufox fetch
    if errorlevel 1 (
        echo [!] Tai ve tai nguyen Camoufox gap su co. Bot van se tu dong chay fallback neu can.
    )
    "%PYTHON_EXE%" -B bot.py
    pause
    goto MENU
)

if "%choice%"=="2" (
    echo.
    echo Dang khoi chay Outlook Register Bot ^(GUI Mode^)...
    if not exist "%~dp0OutlookRegister\bot_outlook.py" (
        echo [!] Khong tim thay OutlookRegister\bot_outlook.py
        pause
        goto MENU
    )
    pushd "%~dp0OutlookRegister"
    "%PYTHON_EXE%" -B bot_outlook.py
    if errorlevel 1 (
        echo [!] Outlook Register Bot vua thoat voi loi. Xem thong bao Python o tren de biet chi tiet.
    )
    pause
    popd
    goto MENU
)

if "%choice%"=="3" (
    echo Dang thoat...
    exit
)

echo Lua chon khong hop le! Vui long nhap lai.
pause
goto MENU