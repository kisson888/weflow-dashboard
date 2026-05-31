' WeFlow Dashboard Silent Launcher
Dim shell, fso, scriptDir, exePath, url
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = scriptDir & "\WeFlowDashboard.exe"
url = "http://127.0.0.1:8765"
If Not fso.FileExists(exePath) Then
  MsgBox "Can't find WeFlowDashboard.exe:" & vbCrLf & exePath, vbCritical, "Start Failed"
  WScript.Quit
End If
' Start server
shell.Run """" & exePath & """", 0, False
' Single PowerShell call for health check loop (avoids launching powershell.exe per iteration)
Dim psCmd
psCmd = "$u='http://127.0.0.1:8765/api/health';Start-Sleep 3;for($i=0;$i -lt 30;$i++){try{$r=Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 1;if($r.StatusCode -eq 200){exit 0}}catch{}Start-Sleep -Milliseconds 500};exit 1"
Dim ret
ret = shell.Run("powershell -Command """ & psCmd & """", 0, True)
If ret = 0 Then
  shell.Run """" & url & """"
Else
  MsgBox "Service start timeout. Open manually:" & vbCrLf & url, vbInformation, "WeFlow Dashboard"
  shell.Run """" & url & """"
End If
