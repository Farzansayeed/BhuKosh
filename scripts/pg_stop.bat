@echo off
rem Stop the portable BhuKosh Postgres instance
setlocal
set REPO=%~dp0..
"%REPO%\infra\pg16\pgsql\bin\pg_ctl.exe" -D "%REPO%\infra\pgdata" stop -m fast
endlocal
