Option Explicit
Dim shell, fso, root, python, base, folder, proc, candidate
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
python = ""
candidate = root & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(candidate) Then python = candidate
base = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python"
If python = "" And fso.FolderExists(base) Then
  For Each folder In fso.GetFolder(base).SubFolders
    candidate = folder.Path & "\pythonw.exe"
    If fso.FileExists(candidate) Then python = candidate
  Next
End If
If python = "" Then
  On Error Resume Next
  Set proc = shell.Exec("where.exe pythonw.exe")
  If Err.Number = 0 Then
    Do Until proc.StdOut.AtEndOfStream
      candidate = Trim(proc.StdOut.ReadLine)
      If InStr(LCase(candidate), "windowsapps") = 0 And fso.FileExists(candidate) Then python = candidate
    Loop
  End If
  On Error GoTo 0
End If
If python = "" Then
  ' No dialog at login. See logs\autostart.log when ALEX does not come up.
Else
  shell.Run Chr(34) & python & Chr(34) & " -m jarvis.launcher --background", 0, False
End If
