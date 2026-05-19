@echo off
setlocal

set PROJECT_DIR=%~dp0..
set PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe
set CHROMA=%PROJECT_DIR%\.venv\Scripts\chroma.exe
set LOGS=%PROJECT_DIR%\logs

if not exist "%LOGS%" mkdir "%LOGS%"

:: Load CHROMA_DB_PATH, CHROMA_HOST, CHROMA_PORT from .env
set CHROMA_DB_PATH=.\data\chromadb
set CHROMA_HOST=127.0.0.1
set CHROMA_PORT=8765

for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_DIR%\.env") do (
    if "%%A"=="CHROMA_DB_PATH" set CHROMA_DB_PATH=%%B
    if "%%A"=="CHROMA_HOST"    set CHROMA_HOST=%%B
    if "%%A"=="CHROMA_PORT"    set CHROMA_PORT=%%B
)

echo [daemon] Starting ChromaDB server on %CHROMA_HOST%:%CHROMA_PORT% ...
start "chroma-daemon" /B "%CHROMA%" run --path "%PROJECT_DIR%\data\chromadb" --host %CHROMA_HOST% --port %CHROMA_PORT% >> "%LOGS%\chroma.log" 2>&1

:: Wait for chroma to be ready (retry up to 15 sec)
set /a TRIES=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a TRIES+=1
"%PYTHON%" -c "import socket,sys; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('%CHROMA_HOST%',%CHROMA_PORT%)); s.close(); sys.exit(r)"
if %errorlevel%==0 goto chroma_ready
if %TRIES% lss 15 goto wait_loop
echo [daemon] ERROR: ChromaDB did not start within 15 seconds. Check logs\chroma.log
exit /b 1

:chroma_ready
echo [daemon] ChromaDB ready. Starting vault watcher ...
start "watcher-daemon" /B "%PYTHON%" "%PROJECT_DIR%\watcher.py" >> "%LOGS%\watcher.log" 2>&1
echo [daemon] All services started. Logs: %LOGS%\
