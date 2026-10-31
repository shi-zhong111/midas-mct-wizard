' MIDAS Wizard - silent launcher (no console window)
' Double-click this file to start the wizard without the console flash.
'
' This file is intentionally pure ASCII. All Python detection /
' verification / installation logic lives in scripts\launch_wizard.ps1,
' which is called in -Silent mode. This script NEVER hunts for pyw.exe
' by itself (the old version did, which caused the Windows
' "Python Launcher is sorry to say ..." dialog on machines where the
' registered Python had been moved or deleted).
Option Explicit
Dim fso, sh, base, ps1Path, scriptPath, batPath, altBatPath, cmdLine
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = base & "\scripts\launch_wizard.ps1"
scriptPath = base & "\midas_wizard.py"
batPath = base & "\launch_wizard.bat"
' The Chinese-named .bat is encoded as ChrW() codes so this file stays pure ASCII
altBatPath = base & "\" & ChrW(&H542F) & ChrW(&H52A8) & ChrW(&H5EFA) & ChrW(&H6A21) & ChrW(&H5BFC) & ChrW(&H5411) & ".bat"

If Not fso.FileExists(scriptPath) Then
  MsgBox "midas_wizard.py was not found next to this launcher." & vbCrLf & _
         "Keep this file in the same folder as the program files.", 48, "MIDAS Wizard"
  WScript.Quit 1
End If

If fso.FileExists(ps1Path) Then
  cmdLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """ -Silent"
  sh.Run cmdLine, 0, False
  WScript.Quit 0
End If

' Fallback: the .bat launcher shows a console and can install Python.
If fso.FileExists(batPath) Then
  sh.Run """" & batPath & """", 1, False
  WScript.Quit 0
End If
If fso.FileExists(altBatPath) Then
  sh.Run """" & altBatPath & """", 1, False
  WScript.Quit 0
End If

MsgBox "The launcher script (scripts\launch_wizard.ps1) is missing." & vbCrLf & _
       "Please unzip the whole portable package again, or install Python 3" & vbCrLf & _
       "from python.org and run launch_wizard.bat.", 48, "MIDAS Wizard"
WScript.Quit 1
