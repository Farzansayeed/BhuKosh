@echo off
rem Start the portable BhuKosh Postgres instance (initializes on first run)
setlocal
set REPO=%~dp0..

rem Local cluster password comes from .env (never hardcode secrets in scripts).
rem Only needed on first run when infra\pgdata does not exist yet.
set "PGPASS="
if exist "%REPO%\.env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%REPO%\.env") do (
    if /i "%%a"=="PG_PASSWORD" set "PGPASS=%%b"
  )
)
if not defined PGPASS (
  echo ERROR: PG_PASSWORD not found in %REPO%\.env
  exit /b 1
)

set PGBIN=%REPO%\infra\pg16\pgsql\bin
if not exist "%REPO%\infra\pgdata" (
  echo Initializing database cluster in infra\pgdata ...
  <nul set /p ="%PGPASS%">"%REPO%\infra\pgpass.tmp"
  "%PGBIN%\initdb.exe" -D "%REPO%\infra\pgdata" -U bhukosh -A scram-sha-256 --pwfile="%REPO%\infra\pgpass.tmp" -E UTF8
  del "%REPO%\infra\pgpass.tmp"
)
if not exist "%REPO%\logs" mkdir "%REPO%\logs"
"%PGBIN%\pg_ctl.exe" -D "%REPO%\infra\pgdata" -l "%REPO%\logs\pg.log" -o "-p 5432" start
endlocal
