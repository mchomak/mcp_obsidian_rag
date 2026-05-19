@echo off
echo [daemon] Stopping watcher and ChromaDB ...

:: Stop watcher (watcher.py in python)
for /f "tokens=2" %%P in ('tasklist /fi "windowtitle eq watcher-daemon" /fo list ^| findstr PID') do taskkill /PID %%P /F >nul 2>&1

:: Stop chroma (window title set by start_daemon.bat)
for /f "tokens=2" %%P in ('tasklist /fi "windowtitle eq chroma-daemon" /fo list ^| findstr PID') do taskkill /PID %%P /F >nul 2>&1

:: Fallback: kill by image name if window-title lookup fails
taskkill /IM chroma.exe /F >nul 2>&1

echo [daemon] Done.
