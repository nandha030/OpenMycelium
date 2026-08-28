@echo off
setlocal EnableDelayedExpansion
REM Put `openmycelium` on this user's PATH and check the runtime is usable.
REM
REM Nothing is installed into the system and nothing is copied: the command
REM stays where the repository is, and this only teaches cmd.exe where to find
REM it. Uninstalling is removing the entry this adds.

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

echo.
echo   installing openmycelium from %HERE%
echo.

if not exist "%HERE%\openmycelium.cmd" (
    echo   ERROR: openmycelium.cmd is not next to this installer.
    exit /b 1
)

REM --- PATH ---------------------------------------------------------------
set "ALREADY="
echo %PATH% | find /i "%HERE%" >nul && set "ALREADY=1"
if defined ALREADY (
    echo   PATH        already contains this directory
) else (
    for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| find /i "Path"') do set "USERPATH=%%B"
    if defined USERPATH (
        echo !USERPATH! | find /i "%HERE%" >nul
        if errorlevel 1 (
            setx PATH "!USERPATH!;%HERE%" >nul && echo   PATH        added %HERE%
        ) else (
            echo   PATH        already registered; open a new terminal to pick it up
        )
    ) else (
        setx PATH "%HERE%" >nul && echo   PATH        added %HERE%
    )
)

REM --- WSL ----------------------------------------------------------------
set "DISTRO=Ubuntu-24.04"
wsl.exe -d %DISTRO% -u root true >nul 2>&1
if errorlevel 1 (
    echo   WSL         ERROR: distro %DISTRO% is not available
    echo               install it with:  wsl --install -d %DISTRO%
    exit /b 2
)
echo   WSL         %DISTRO% reachable

for %%V in (/opt/hetenv/bin/python /opt/rocmenv/bin/python) do (
    wsl.exe -d %DISTRO% -u root test -x %%V >nul 2>&1
    if errorlevel 1 (
        echo   runtime     ERROR: %%V is missing
        echo               the CUDA and ROCm environments must exist inside WSL
        exit /b 3
    )
)
echo   runtime     CUDA and ROCm python environments present

echo.
echo   done. Open a NEW terminal, then:
echo.
echo     openmycelium doctor
echo     openmycelium models import "C:\Users\User\Downloads\Models\Mistral-Nemo-Instruct-2407"
echo     openmycelium run --model mistral-nemo --prompt "Explain cross-vendor GPU inference" --max-new-tokens 64
echo.
exit /b 0
