' MIDAS Wizard - silent launcher (no black console window)
' Double-click this file to start the wizard without the console flash.
Option Explicit
Dim fso, sh, base, script, exe, usePyw, oExec, line
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
script = base & "\midas_wizard.py"
If Not fso.FileExists(script) Then
  MsgBox "midas_wizard.py was not found next to this launcher." & vbCrLf & _
         "Keep this file in the same folder as the program files.", 48, "MIDAS Wizard"
  WScript.Quit 1
End If

exe = ""
usePyw = False

' 1) pythonw next to this file, or well-known location
If fso.FileExists(base & "\pythonw.exe") Then exe = base & "\pythonw.exe"
If exe = "" And fso.FileExists("F:\python\pythonw.exe") Then exe = "F:\python\pythonw.exe"

' 2) pythonw on PATH
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

' 3) pyw launcher on PATH
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
  MsgBox "Python 3 was not found." & vbCrLf & vbCrLf & _
         "Please double-click the .bat launcher instead - it can install Python for you.", _
         48, "MIDAS Wizard"
  WScript.Quit 1
End If

If usePyw Then
  sh.Run """" & exe & """ """ & script & """", 0, False
Else
  sh.Run """" & exe & """ """ & script & """", 0, False
End If
WScript.Quit 0