' WeFlow Dashboard - Hidden Startup
' Double-click this file to start the server WITHOUT a console window.
' The browser will open automatically.
' To stop: use the "Stop Service" button in the web dashboard.

Dim shell, fso, scriptDir
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get the directory where this VBS script is located
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Start the Python server with window hidden (0 = Hide)
' Try multiple Python executable names
On Error Resume Next
shell.Run "cmd /c python """ & scriptDir & "\dashboard_server.py""", 0, False
If Err.Number <> 0 Then
    shell.Run "cmd /c python3 """ & scriptDir & "\dashboard_server.py""", 0, False
End If
On Error Goto 0

' Wait for server to start
WScript.Sleep 2000

' Open browser
shell.Run "http://127.0.0.1:8765"
