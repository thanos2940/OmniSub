' OmbiSub System Tray App Windowless Launcher
' Launches pythonw.exe backend\tray_app.py with window style 0 (no console window)

Option Explicit

Dim WshShell, fso, scriptDir, repoRoot, pythonwExe, trayApp, cmd

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' deploy\windows is two directories below repo root
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(scriptDir))

pythonwExe = repoRoot & "\.venv\Scripts\pythonw.exe"
trayApp = repoRoot & "\backend\tray_app.py"

If Not fso.FileExists(pythonwExe) Then
    pythonwExe = "pythonw.exe"
End If

cmd = """" & pythonwExe & """ """ & trayApp & """"
' Pass on our own arguments (install.ps1 gives --port and --data-dir).
Dim i
For i = 0 To WScript.Arguments.Count - 1
    cmd = cmd & " """ & WScript.Arguments(i) & """"
Next

WshShell.CurrentDirectory = repoRoot
WshShell.Run cmd, 0, False

Set WshShell = Nothing
Set fso = Nothing
