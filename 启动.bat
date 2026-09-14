@echo off
rem 本地 AI Agent —— 唯一启动入口。双击后自动启动全部服务并打开聊天页面。
rem 日常使用只看 Web 页面；退出请点页面上的“退出”或直接关闭页面。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-daily.ps1"
if errorlevel 1 pause
