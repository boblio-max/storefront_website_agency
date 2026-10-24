@echo off
REM install_inbox_watcher.bat — hands-free Gmail polling on Windows.
REM Registers a Scheduled Task that runs the inbox watcher at logon and
REM repeats every 5 minutes forever. Run once, then never touch it again.
REM
REM   install_inbox_watcher.bat
REM   schtasks /query /tn "StorefrontInboxWatcher"   (verify)
REM   schtasks /delete /tn "StorefrontInboxWatcher" /f   (uninstall)
setlocal
set REPO=%~dp0
set PY=py
set TASK=StorefrontInboxWatcher
set CMD="%PY% \"%REPO%inbox_watcher.py\" --daemon --interval 300 --send-emails"

echo [watcher] repo: %REPO%
echo [watcher] registering scheduled task "%TASK%" ...
echo [watcher] command: %CMD%

schtasks /create /tn "%TASK%" /tr "%CMD%" /sc onlogon /rl limited /f
if errorlevel 1 (
  echo [watcher] FAILED to create logon task. Try running this file as Administrator.
  exit /b 1
)

REM Also add a 5-minute repetition trigger so polling survives sleep/resume.
schtasks /create /tn "%TASK%_5min" /tr "%CMD%" /sc minute /mo 5 /f
if errorlevel 1 (
  echo [watcher] note: 5-min trigger failed (logon trigger still active).
) else (
  echo [watcher] 5-minute repeat trigger registered.
)

echo [watcher] done. The inbox will now poll itself every 5 minutes.
echo [watcher] Logs: inbox_watcher.log + inbox_state.json in %REPO%
echo [watcher] Needs AGENCY_IMAP_PASS in .env (Gmail 2FA App Password).
endlocal
