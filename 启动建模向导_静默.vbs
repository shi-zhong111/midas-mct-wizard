' MIDAS Wizard - silent launcher (no black console window)
' Double-click this file to start the wizard without the console flash.
' If Python is not installed yet, it hands over to the .bat launcher (which can install Python).
Option Explicit
Dim fso, sh, base, script, exe, oExec, line, tf, batPath, usePyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
script = base & "\midas_wizard.py"
batPath = base & "\启动建模向导.bat"
If Not fso.FileExists(script) Then
  MsgBox "midas_wizard.py was not found next to this launcher." & vbCrLf & _
         "Keep this file in the same folder as the program files.", 48, "MIDAS Wizard"
  WScript.Quit 1
End If

exe = ""
usePyw = False

' 1) pythonw.exe remembered by the installer / bat launcher
If fso.FileExists(base & "\pythonw_path.txt") Then
  On Error Resume Next
  Set tf = fso.OpenTextFile(base & "\pythonw_path.txt", 1)
  If Err.Number = 0 Then
    line = Trim(tf.ReadLine())
    tf.Close
    If line <> "" And fso.FileExists(line) Then exe = line
  End If
  On Error GoTo 0
End If

' 2) pythonw next to this file, or the local dev path
If exe = "" And fso.FileExists(base & "\pythonw.exe") Then exe = base & "\pythonw.exe"
If exe = "" And fso.FileExists("F:\python\pythonw.exe") Then exe = "F:\python\pythonw.exe"

' 3) pythonw on PATH
If exe = "" Then
  On Error Resume Next
  Set oExec = sh.Exec("cmd /c where pythonw")
  If Err.Number = 0 Then
    line = oExec.StdOut.ReadLine()
    If Not IsNull(line) Then
      line = Trim(line)
      If line <> "" And fso.FileExists(line) Then exe = line
    End If
  End If
  On Error GoTo 0
End If

' 4) pyw launcher on PATH
If exe = "" Then
  On Error Resume Next
  Set oExec = sh.Exec("cmd /c where pyw")
  If Err.Number = 0 Then
    line = oExec.StdOut.ReadLine()
    If Not IsNull(line) Then
      line = Trim(line)
      If line <> "" And fso.FileExists(line) Then
        exe = line
        usePyw = True
      End If
    End If
  End If
  On Error GoTo 0
End If

If exe = "" Then
  ' No Python yet: let the .bat launcher handle installation (it shows its console).
  If fso.FileExists(batPath) Then
    sh.Run """" & batPath & """", 1, False
  Else
    MsgBox "Python 3 was not found and the .bat launcher is missing." & vbCrLf & _
           "Please install Python 3 from python.org, then try again.", 48, "MIDAS Wizard"
  End If
  WScript.Quit 0
End If

sh.Run """" & exe & """ """ & script & """", 0, False
WScript.Quit 0