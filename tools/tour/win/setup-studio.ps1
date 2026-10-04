# Run once as an administrator (over SSH as the profile user) in the windows11-studio copy of windows-11.
# The demo password is a throwaway for this studio VM only, like lab/lab in the tracked profiles.
$ErrorActionPreference = 'Stop'
if (-not (Get-LocalUser -Name demo -ErrorAction SilentlyContinue)) {
  New-LocalUser -Name demo -Password (ConvertTo-SecureString 'demo' -AsPlainText -Force) -FullName 'demo' -PasswordNeverExpires | Out-Null
}
$admins = (Get-LocalGroup -SID 'S-1-5-32-544').Name
if (-not (Get-LocalGroupMember -Group $admins -Member demo -ErrorAction SilentlyContinue)) { Add-LocalGroupMember -Group $admins -Member demo }
$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $k AutoAdminLogon '1'; Set-ItemProperty $k DefaultUserName 'demo'; Set-ItemProperty $k DefaultPassword 'demo'; Set-ItemProperty $k DefaultDomainName $env:COMPUTERNAME
New-Item -ItemType Directory -Force C:\tools | Out-Null
if (-not (Test-Path C:\tools\ffmpeg\bin\ffmpeg.exe)) {
  $z = "$env:TEMP\ffmpeg.zip"
  Invoke-WebRequest -UseBasicParsing 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile $z
  Expand-Archive $z C:\tools -Force; Remove-Item $z
  Get-ChildItem C:\tools -Directory -Filter 'ffmpeg-*' | Select-Object -First 1 | Rename-Item -NewName ffmpeg
}
# Studio only: administrators elevate without the consent prompt (the automation cannot answer the secure desktop).
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' ConsentPromptBehaviorAdmin 0
# No lock screen, no screen saver, no sleep while recording.
New-Item -Force 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' NoLockScreen 1
powercfg /change monitor-timeout-ac 0; powercfg /change standby-timeout-ac 0
# Edge opens the dashboard: no first-run pages (four of them, sign-in, Google import, consent).
New-Item -Force 'HKLM:\SOFTWARE\Policies\Microsoft\Edge' | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Edge' HideFirstRunExperience 1
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Edge' TranslateEnabled 0
"studio ready"
