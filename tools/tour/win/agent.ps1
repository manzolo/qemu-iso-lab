# Runs in demo's interactive session (scheduled task at logon): executes every C:\studio\q\*.ps1
# in name order, writes its output to C:\studio\done\<name>.log and removes it. The host drops the files.
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Studio {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)] public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName; public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields, dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput; public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName; public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency, dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight; }
  [DllImport("user32.dll")] public static extern int EnumDisplaySettings(string d, int m, ref DEVMODE dm);
  [DllImport("user32.dll")] public static extern int ChangeDisplaySettings(ref DEVMODE dm, int f);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, int f, IntPtr e);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  // A background process may not take the foreground; a synthetic Alt press lifts that lock.
  public static bool Front(IntPtr h) { keybd_event(0x12, 0, 0, IntPtr.Zero); keybd_event(0x12, 0, 2, IntPtr.Zero); ShowWindow(h, 9); SetForegroundWindow(h); return GetForegroundWindow() == h; }
  public static int Resolution(int w, int h) { DEVMODE dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(dm); EnumDisplaySettings(null, -1, ref dm); dm.dmPelsWidth = w; dm.dmPelsHeight = h; dm.dmFields = 0x80000 | 0x100000; return ChangeDisplaySettings(ref dm, 1); }
}
"@

# --- helpers the queued scripts call (they run in this scope's children) ---
function Esc([string]$t) { ($t.ToCharArray() | ForEach-Object { if ('+^%~(){}[]'.Contains([string]$_)) { '{' + $_ + '}' } else { [string]$_ } }) -join '' }
function TypeIn([string]$text, [int]$ms = 40) {
  # SendWait costs ~0.3 s a call, so one call per word (spaces kept), paced by its length.
  foreach ($w in [regex]::Split($text, '(?<= )')) { if ($w) { [System.Windows.Forms.SendKeys]::SendWait((Esc $w)); Start-Sleep -Milliseconds ($ms * $w.Length / 2) } }
}
function Focus($target) {
  # A process id, or a substring of a window title. Esc only when the window is not already in front
  # (it closes a Start menu left open after logon), never into a terminal that has the keyboard:
  # a Linux program reads it as ^[ and the next answer arrives corrupted.
  $p = if ($target -is [int]) { Get-Process -Id $target } else { Get-Process | Where-Object { $_.MainWindowTitle -like "*$target*" } | Select-Object -First 1 }
  for ($i = 0; $i -lt 20 -and $p -and $p.MainWindowHandle -eq 0; $i++) { Start-Sleep -Milliseconds 250; $p.Refresh() }
  if (-not $p -or $p.MainWindowHandle -eq 0) { return "focus ${target}: no window" }
  if ([Studio]::GetForegroundWindow() -eq $p.MainWindowHandle) { return "focus ${target}: already" }
  [System.Windows.Forms.SendKeys]::SendWait('{ESC}'); Start-Sleep -Milliseconds 300
  $ok = [Studio]::Front($p.MainWindowHandle); Start-Sleep -Milliseconds 300; "focus ${target}: $ok"
}
function Key([string]$spec) { [System.Windows.Forms.SendKeys]::SendWait($spec) }
function Glide([int]$x, [int]$y, [double]$sec = 0.6) {
  $p = [System.Windows.Forms.Cursor]::Position; $n = [Math]::Max(1, [int]($sec * 60))
  for ($i = 1; $i -le $n; $i++) { $k = $i / $n; $k = $k * $k * (3 - 2 * $k)
    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point ([int]($p.X + ($x - $p.X) * $k)), ([int]($p.Y + ($y - $p.Y) * $k)); Start-Sleep -Milliseconds 16 }
}
function RecStart([string]$out) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo 'C:\tools\ffmpeg\bin\ffmpeg.exe'
  $psi.Arguments = "-hide_banner -loglevel error -y -f gdigrab -framerate 25 -draw_mouse 1 -i desktop -c:v libx264 -preset ultrafast -crf 16 -pix_fmt yuv420p `"$out`""
  $psi.UseShellExecute = $false; $psi.RedirectStandardInput = $true; $psi.CreateNoWindow = $true
  $global:rec = [System.Diagnostics.Process]::Start($psi); $global:recStart = Get-Date
  "rec $($global:rec.Id) $out"
}
function RecStop { if ($global:rec) { $global:rec.StandardInput.Write('q'); $global:rec.WaitForExit(30000) | Out-Null; "stopped $([int]((Get-Date) - $global:recStart).TotalSeconds)s"; $global:rec = $null } }
function RecTime { if ($global:rec) { [Math]::Round(((Get-Date) - $global:recStart).TotalSeconds, 1) } else { -1 } }

$q = 'C:\studio\q'; $done = 'C:\studio\done'
New-Item -ItemType Directory -Force $q, $done | Out-Null
"agent up $(Get-Date -Format o)" | Set-Content "$done\agent.log"
while ($true) {
  foreach ($f in Get-ChildItem $q -Filter *.ps1 | Sort-Object Name) {
    $log = Join-Path $done ($f.BaseName + '.log')
    try { (& $f.FullName 2>&1 | Out-String) | Set-Content $log } catch { "ERROR: $_" | Set-Content $log }
    Remove-Item $f.FullName -Force
  }
  Start-Sleep -Milliseconds 200
}
