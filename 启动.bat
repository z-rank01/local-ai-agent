@echo off
rem Single entry point for the Local AI Agent.
rem Starts all services and opens the chat page.
rem Exit from the Web page or just close the page; everything stops itself.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-daily.ps1"
if errorlevel 1 pause
