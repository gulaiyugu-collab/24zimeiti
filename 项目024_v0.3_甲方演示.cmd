@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo-v03.ps1" %*
if errorlevel 1 pause
