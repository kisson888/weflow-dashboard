@echo off
title WeFlow Dashboard
setlocal
set "SCRIPT_DIR=%~dp0"
echo ========================================
echo   WeFlow Monitor Dashboard Server
echo ========================================
echo.
echo Starting server...
echo.
start "" /B "%SCRIPT_DIR%WeFlowDashboard.exe"
echo Waiting for service ready...

REM Use a single PowerShell call that does the entire health check loop
REM (avoids starting powershell.exe 30+ times individually)
powershell -Command ^
  $url='http://127.0.0.1:8765/api/health'; ^
  Start-Sleep -Seconds 3; ^
  for($i=0;$i -lt 40;$i++){ ^
    try{ ^
      $r=Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 1; ^
      if($r.StatusCode -eq 200){exit 0} ^
    }catch{} ^
    Start-Sleep -Milliseconds 500 ^
  }; ^
  exit 1

if %ERRORLEVEL% equ 0 (
  echo [OK] Service is ready
) else (
  echo [WARN] Service start timeout, check WeFlowDashboard.exe
)
:open
echo.
echo ^>^> Opening http://127.0.0.1:8765
start "" "http://127.0.0.1:8765"
echo.
echo Press any key to close...
pause >nul
