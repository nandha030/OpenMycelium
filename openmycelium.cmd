@echo off
setlocal EnableDelayedExpansion
REM openmycelium - Windows launcher.
REM
REM This file is a wrapper and nothing more. It locates the *installed*
REM `openmycelium` command inside WSL and hands it the arguments; every decision
REM about what a command does lives in the installed package, which can be
REM tested and versioned. An earlier version of this file pointed at a source
REM checkout and a hand-built interpreter, so it ran development code on one
REM machine and nothing anywhere else.
REM
REM It also holds ONE foreground wsl.exe for the life of `run`, `chat` and
REM `serve`, because WSL2 stops the VM when its last Windows client
REM disconnects. Closing this window unloads the model; that is intended and
REM visible rather than a silent death.

REM --- which distribution? -----------------------------------------------
REM There is deliberately no default. This file used to assume "Ubuntu-24.04",
REM which meant a launch could silently go to a distribution that had never
REM been provisioned -- the name exists on many machines and means nothing in
REM particular. If exactly one distribution is installed, use it; otherwise
REM ask, listing what is there.
set "DISTRO=%OPENMYCELIUM_WSL_DISTRO%"
if not defined DISTRO (
    set "COUNT=0"
    for /f "usebackq delims=" %%D in (`wsl.exe --list --quiet 2^>nul`) do (
        set "NAME=%%D"
        set "NAME=!NAME: =!"
        if /i not "!NAME!"=="docker-desktop" if /i not "!NAME!"=="docker-desktop-data" if not "!NAME!"=="" (
            set /a COUNT+=1
            set "ONLY=!NAME!"
        )
    )
    if "!COUNT!"=="1" (
        set "DISTRO=!ONLY!"
    ) else (
        echo.
        echo   Which WSL distribution should OpenMycelium run in?
        echo   More than one is installed and none was selected:
        echo.
        for /f "usebackq delims=" %%D in (`wsl.exe --list --quiet 2^>nul`) do echo       %%D
        echo.
        echo   Choose one with:      set OPENMYCELIUM_WSL_DISTRO=your-distro
        echo.
        exit /b 78
    )
)

REM --- is WSL there at all? ----------------------------------------------
wsl.exe -d %DISTRO% -u root true >nul 2>&1
if errorlevel 1 (
    echo.
    echo   OpenMycelium could not reach WSL distribution "%DISTRO%".
    echo.
    echo   Install it with:      wsl --install -d %DISTRO%
    echo   Or choose another:    set OPENMYCELIUM_WSL_DISTRO=your-distro
    echo.
    exit /b 69
)

REM --- is the package installed there? -----------------------------------
REM `command -v` is asked inside a login shell so a venv on the user's PATH is
REM found the same way it would be if they ran it themselves.
for /f "usebackq delims=" %%R in (`wsl.exe -d %DISTRO% -u root bash -lc "command -v openmycelium 2>/dev/null"`) do set "OMBIN=%%R"
if not defined OMBIN (
    if defined OPENMYCELIUM_VENV (
        wsl.exe -d %DISTRO% -u root test -x "%OPENMYCELIUM_VENV%/bin/openmycelium" >nul 2>&1
        if not errorlevel 1 set "OMBIN=%OPENMYCELIUM_VENV%/bin/openmycelium"
    )
)
if not defined OMBIN (
    echo.
    echo   OpenMycelium is not installed in WSL distribution "%DISTRO%".
    echo.
    echo   Install it there:
    echo     wsl -d %DISTRO% -u root python3 -m venv /opt/openmycelium/venv
    echo     wsl -d %DISTRO% -u root /opt/openmycelium/venv/bin/pip install openmycelium
    echo.
    echo   Then either put it on PATH inside WSL, or point at it:
    echo     set OPENMYCELIUM_VENV=/opt/openmycelium/venv
    echo.
    echo   After installing, provision the GPU environments:
    echo     openmycelium provision
    echo.
    exit /b 70
)

REM --- pass everything through, quoted, from any directory ---------------
set "ARGS="
:collect
if "%~1"=="" goto :launch
set "ARGS=!ARGS! "%~1""
shift
goto :collect

:launch
if not defined ARGS (
    wsl.exe -d %DISTRO% -u root "%OMBIN%"
    exit /b %ERRORLEVEL%
)
wsl.exe -d %DISTRO% -u root "%OMBIN%"!ARGS!
exit /b %ERRORLEVEL%
