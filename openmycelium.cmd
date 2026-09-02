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
REM `wsl.exe --list --quiet` writes UTF-16LE. `for /f` reads it as ANSI and
REM stops at the first null byte, so the loop below used to see a single token
REM -- the letter "d" from "docker-desktop" -- count one distribution, skip the
REM prompt, and run `wsl -d d`. Every launch then failed with
REM WSL_E_DISTRO_NOT_FOUND unless OPENMYCELIUM_WSL_DISTRO happened to be set,
REM which made the launcher look fine to anyone who had set it once.
REM
REM The list is converted to ANSI first. `chcp` alone does not help: the encoding
REM is in the pipe, not the console.
set "DISTRO=%OPENMYCELIUM_WSL_DISTRO%"
if not defined DISTRO (
    set "OMLIST=%TEMP%\openmycelium-distros.txt"
    wsl.exe --list --quiet 2>nul > "!OMLIST!.utf16"
    powershell.exe -NoProfile -Command ^
        "Get-Content -Encoding Unicode '!OMLIST!.utf16' | Where-Object { $_.Trim() -ne '' } | Set-Content -Encoding ASCII '!OMLIST!'" >nul 2>&1
    set "COUNT=0"
    for /f "usebackq delims=" %%D in ("!OMLIST!") do (
        set "NAME=%%D"
        set "NAME=!NAME: =!"
        if /i not "!NAME!"=="docker-desktop" if /i not "!NAME!"=="docker-desktop-data" if not "!NAME!"=="" (
            set /a COUNT+=1
            set "ONLY=!NAME!"
        )
    )
    del "!OMLIST!.utf16" >nul 2>&1
    if "!COUNT!"=="1" (
        set "DISTRO=!ONLY!"
    ) else (
        echo.
        echo   Which WSL distribution should OpenMycelium run in?
        if "!COUNT!"=="0" (
            echo   None could be read from `wsl --list --quiet`.
        ) else (
            echo   More than one is installed and none was selected:
            echo.
            for /f "usebackq delims=" %%D in ("!OMLIST!") do echo       %%D
        )
        echo.
        echo   Choose one with:      set OPENMYCELIUM_WSL_DISTRO=your-distro
        echo.
        del "!OMLIST!" >nul 2>&1
        exit /b 78
    )
    del "!OMLIST!" >nul 2>&1
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
REM A venv is not on root's login PATH on any machine tested, so `command -v`
REM finds nothing and the launcher depended on OPENMYCELIUM_VENV being set --
REM which the error text below never told anyone to set for the common install
REM locations. Look in them before giving up.
if not defined OMBIN (
    for %%V in (/opt/om/venv /opt/openmycelium/venv /opt/omfresh/venv) do (
        if not defined OMBIN (
            wsl.exe -d %DISTRO% -u root test -x "%%V/bin/openmycelium" >nul 2>&1
            if not errorlevel 1 set "OMBIN=%%V/bin/openmycelium"
        )
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

REM --- carry the variables the runtime reads into WSL ---------------------
REM
REM wsl.exe forwards nothing unless WSLENV names it. Without this, the console's
REM own remediation -- "set OM_QUALIFICATION_MODE=qualify and re-run" -- is
REM unfollowable through this launcher: the variables are set in Windows, the
REM process reads none of them, and the run is refused with the message that
REM told you to set them. Same for OM_SAFETY_MODE, which shadow mode reads.
REM
REM Appended to any WSLENV the caller already has rather than replacing it.
set "OMVARS=OM_QUALIFICATION_MODE:OM_QUALIFICATION_ACTOR:OM_QUALIFICATION_REASON:OM_SAFETY_MODE"
if defined WSLENV (
    set "WSLENV=%WSLENV%:%OMVARS%"
) else (
    set "WSLENV=%OMVARS%"
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
