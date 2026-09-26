<#
.SYNOPSIS
  QEMU ISO Lab on Windows 11: WSL2 + Ubuntu, the repository inside it, ./setup.sh, then the web dashboard.

.DESCRIPTION
  vmctl needs Linux (KVM, /proc, Unix sockets), so on Windows it lives in a WSL2 distribution,
  which gets /dev/kvm through nested virtualization. This script does the Windows side and then
  runs the normal Linux Quick Start inside the distribution:

    1. installs WSL and the distribution if missing (asks to reboot when Windows needs it);
    2. makes sure nested virtualization is on in %UserProfile%\.wslconfig;
    3. clones the repository into ~/qemu-iso-lab in the distribution (or pulls it) and runs
       ./setup.sh there (sudo asks for the Linux password you chose at the first start);
    4. puts the Linux user in the kvm group;
    5. with -Web, starts `vmctl web` in WSL and opens the printed URL in the Windows browser.

  Run it again at any time: every step skips what is already done.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
.EXAMPLE
  .\setup-windows.ps1 -Web          # only start the dashboard (after the first setup)
#>
param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$Repo = "https://github.com/manzolo/qemu-iso-lab.git",
    [switch]$Web
)

$ErrorActionPreference = "Stop"

function Say([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Warn([string]$text) { Write-Host "    $text" -ForegroundColor Yellow }
function InDistro([string]$command) {
    # One bash login shell in the distribution; its exit code comes back as $LASTEXITCODE.
    & wsl.exe -d $Distro -- bash -lc $command
}

function Start-Dashboard {
    Say "Starting the dashboard in $Distro (Ctrl-C here stops the server; jobs keep running)"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "wsl.exe"
    $psi.Arguments = "-d $Distro -- bash -lc `"cd ~/qemu-iso-lab && exec ./bin/vmctl web`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    $opened = $false
    while (-not $process.StandardOutput.EndOfStream) {
        $line = $process.StandardOutput.ReadLine()
        Write-Host $line
        # WSL forwards localhost: the URL vmctl prints works as is in the Windows browser.
        if (-not $opened -and $line -match "(http://127\.0\.0\.1:\d+/\?token=\S+)") {
            Start-Process $Matches[1]
            $opened = $true
        }
    }
}

if ($Web) { Start-Dashboard; exit 0 }

# 1. WSL and the distribution -------------------------------------------------------------
Say "Checking WSL"
$wslReady = $true
try { & wsl.exe --status *> $null; if ($LASTEXITCODE -ne 0) { $wslReady = $false } } catch { $wslReady = $false }
$distros = @()
if ($wslReady) { $distros = (& wsl.exe -l -q) -replace "`0", "" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() } }
if (-not $wslReady -or -not ($distros -contains $Distro)) {
    Say "Installing WSL with $Distro (Windows may ask for administrator rights)"
    & wsl.exe --install -d $Distro
    Warn "If Windows asked to restart, restart and run this script again."
    Warn "At the first start of $Distro choose a Linux user name and password: sudo will ask for it."
    exit 0
}

# 2. Nested virtualization (KVM inside WSL) ------------------------------------------------
$config = Join-Path $env:UserProfile ".wslconfig"
$text = if (Test-Path $config) { (Get-Content $config -Raw) -replace "`r`n", "`n" } else { "" }
if ($text -notmatch "(?im)^[ \t]*nestedVirtualization[ \t]*=[ \t]*true") {
    Say "Enabling nested virtualization in $config"
    if ($text -match "(?im)^[ \t]*\[wsl2\]") {
        $text = $text -replace "(?im)^[ \t]*nestedVirtualization[ \t]*=.*\n?", ""
        $text = $text -replace "(?im)^([ \t]*\[wsl2\][ \t]*)$", "`$1`nnestedVirtualization=true"
    } else {
        $text = $text.TrimEnd() + "`n[wsl2]`nnestedVirtualization=true`n"
    }
    Set-Content -Path $config -Value ($text.Trim() -replace "`n", "`r`n") -Encoding ASCII
    & wsl.exe --shutdown
}

# 3. The repository and ./setup.sh inside the distribution ----------------------------------
Say "Getting the repository into ~/qemu-iso-lab ($Distro)"
InDistro "command -v git >/dev/null || { sudo apt-get update -q && sudo apt-get install -y -q git; }"
InDistro "if [ -d ~/qemu-iso-lab/.git ]; then git -C ~/qemu-iso-lab pull --ff-only; else git clone $Repo ~/qemu-iso-lab; fi"
if ($LASTEXITCODE -ne 0) { throw "git clone/pull failed in $Distro" }

Say "Running ./setup.sh in $Distro (it lists what it installs and asks first)"
InDistro "cd ~/qemu-iso-lab && ./setup.sh"
if ($LASTEXITCODE -ne 0) { throw "./setup.sh failed in $Distro" }

# 4. /dev/kvm for the Linux user -------------------------------------------------------------
InDistro "test -e /dev/kvm"
if ($LASTEXITCODE -ne 0) {
    Warn "/dev/kvm is missing in ${Distro}: guests would run without acceleration."
    Warn "Enable virtualization in the firmware (BIOS/UEFI) and check that Windows 11 is up to date."
} else {
    InDistro "test -w /dev/kvm || sudo usermod -aG kvm `$USER"
    & wsl.exe --terminate $Distro  # the new group applies at the next start
}

Say "Done. Start the dashboard with:"
Write-Host "    .\setup-windows.ps1 -Web" -ForegroundColor Green
Write-Host "  or, inside ${Distro}:  cd ~/qemu-iso-lab && ./bin/vmctl web   (open the printed URL in any Windows browser)"
