@echo off
title Lead Finder dashboard
rem Starts the Google Maps scraper (if it is not already running) and the Lead Finder dashboard,
rem then opens http://127.0.0.1:8090 in your browser. Close this window to stop the dashboard.
python "%~dp0dashboard\server.py" %*
if errorlevel 9009 py -3 "%~dp0dashboard\server.py" %*
if errorlevel 1 pause
