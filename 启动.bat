@echo off
rem Single entry point for the Local AI Agent.
rem Starts all services and opens the chat page (browser tab or desktop window).
rem Exit from the page or just close it; everything stops itself.
rem Pass arguments (e.g. -Desktop / -Build / -SkipTools / -RebuildWeb) to skip the prompt.
set "UI_MODE="
if not "%~1"=="" goto :run
choice /c WD /n /t 10 /d W /m "UI mode: [W] Browser (default, auto-picked in 10s) / [D] Desktop window"
if errorlevel 2 set "UI_MODE=-Desktop"
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-daily.ps1" %UI_MODE% %*
if errorlevel 1 pause
