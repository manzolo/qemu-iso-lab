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
    5. creates desktop and Start menu shortcuts; with -Web, opens the dashboard.

  Run it again at any time: every step skips what is already done.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
.EXAMPLE
  .\setup-windows.ps1 -Web          # only start the dashboard (after the first setup)
#>
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
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
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Dashboard exited with code $($process.ExitCode)." }
}

function Install-Shortcuts {
    $dir = Join-Path $env:LOCALAPPDATA "QemuIsoLab"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $savedScript = Join-Path $dir "setup-windows.ps1"
    if ($PSCommandPath) {
        if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($savedScript)) {
            Copy-Item -LiteralPath $PSCommandPath -Destination $savedScript -Force
        }
    } else {
        # The PowerShell one-liner has no script file to retain for future launches.
        Invoke-WebRequest -UseBasicParsing "https://manzolo.github.io/qemu-iso-lab/install.ps1" -OutFile $savedScript
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        $link = $shell.CreateShortcut((Join-Path $folder "QEMU ISO Lab.lnk"))
        $link.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $link.Arguments = "-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File `"$savedScript`" -Distro `"$Distro`" -Web"
        $link.WorkingDirectory = $dir
        $link.Description = "Open the QEMU ISO Lab dashboard"
        $link.Save()
    }
}

if ($Web) { Start-Dashboard; return }

# 1. WSL and the distribution -------------------------------------------------------------
Say "Checking WSL"
$wslReady = $true
try { & wsl.exe --status *> $null; if ($LASTEXITCODE -ne 0) { $wslReady = $false } } catch { $wslReady = $false }
$distros = @()
if ($wslReady) { $distros = (& wsl.exe -l -q) -replace "`0", "" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() } }
if (-not $wslReady) {
    Say "Installing WSL (accept the Windows administrator prompt)"
    # Elevate only the system installation. Install Ubuntu afterwards as the original user,
    # even if UAC needed credentials belonging to a different administrator account.
    $process = Start-Process -FilePath "wsl.exe" -ArgumentList "--install --no-distribution" -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "WSL installation failed (exit $($process.ExitCode))." }
    Warn "Restart Windows if requested, then run the same installer again to install Ubuntu."
    return
}
if (-not ($distros -contains $Distro)) {
    Say "Installing $Distro for your Windows account"
    & wsl.exe --set-default-version 2
    if ($LASTEXITCODE -ne 0) { throw "Could not select WSL2. Restart Windows, then run the installer again." }
    & wsl.exe --install -d $Distro
    if ($LASTEXITCODE -notin @(0, 3010)) { throw "WSL installation failed (exit $LASTEXITCODE). Try from an administrator PowerShell." }
    Warn "If Windows asked to restart, restart and run this script again."
    Warn "At the first start of $Distro choose a Linux user name and password: sudo will ask for it."
    Warn "Then run the same installer command again to finish setup."
    return
}
# Convert only a WSL1 distribution: `--set-version` on one that is already WSL2 answers "already
# the requested version" with a non-zero exit (WSL_E_VM_MODE_INVALID_STATE, WSL 3.0.1), which
# ended the third run of the installer on a fresh Windows 11 (verified live 2026-10-05).
$versions = (& wsl.exe -l -v) -replace "`0", ""
$row = $versions | Where-Object { $_ -match ("^\*?\s*" + [regex]::Escape($Distro) + "\s") } | Select-Object -First 1
if ($row -and $row -notmatch "\s2\s*$") {
    & wsl.exe --set-version $Distro 2
    if ($LASTEXITCODE -ne 0) { throw "Could not configure $Distro to use WSL2." }
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
if ($LASTEXITCODE -ne 0) { throw "Installing git failed in $Distro" }
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
    if ($LASTEXITCODE -ne 0) { throw "Could not grant access to KVM in $Distro" }
    & wsl.exe --terminate $Distro  # the new group applies at the next start
}

Install-Shortcuts
Say "Done. Double-click QEMU ISO Lab on your desktop or in the Start menu."
Say "You can also start the dashboard with:"
Write-Host "    wsl -d $Distro -- bash -lc 'cd ~/qemu-iso-lab && ./bin/vmctl web'" -ForegroundColor Green
Write-Host "  or, inside ${Distro}:  cd ~/qemu-iso-lab && ./bin/vmctl web   (open the printed URL in any Windows browser)"
