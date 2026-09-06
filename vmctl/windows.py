"""Windows unattended install helpers: autounattend.xml, first-logon script, seed ISO, prompt-free ISO.

The technique is the one proven in the kvm-lab repository (scripts/win11/create_win11_vm.sh),
adapted to plain QEMU:

1. The Microsoft ISO is rebuilt once with ``efisys_noprompt.bin`` as the UEFI El Torito
   image, so the guest never waits at "Press any key to boot from CD or DVD". The result is
   cached next to the other ISOs as ``<stem>-noprompt.iso`` and does not depend on the profile.
2. ``autounattend.xml`` and ``vmctl-setup.ps1`` are packed into a small ``VMCTLSEED`` ISO.
   Windows Setup searches the root of every removable drive for an answer file, so the seed
   rides as a plain SATA CD-ROM next to the install ISO and the virtio-win driver ISO: the
   6 GB ISO is never touched again when the profile changes.
3. The answer file wipes disk 0 (GPT: EFI + MSR + Windows), injects the virtio storage and
   network drivers from the virtio-win CD while still in WinPE, bypasses the TPM / Secure
   Boot / CPU / RAM checks, installs the requested edition, creates the local administrator
   and enables autologon; ``FirstLogonCommands`` launch ``vmctl-setup.ps1`` from the seed CD.
   The OOBE block must NOT use the deprecated ``SkipMachineOOBE``/``SkipUserOOBE``: on
   Windows 10 they skip the msoobe stage that executes the FirstLogonCommands RunOnce entry
   (it stayed in the registry across reboots, UAC on or off), while Windows 11 ignores them.
4. The script installs the virtio guest tools, OpenSSH Server (with the project's public key
   in ``administrators_authorized_keys``), the profile's PowerShell ``setup_commands``, then
   writes the completion token on COM1 and shuts the machine down. COM1 is the serial
   console QEMU exposes on stdio, so ``run_and_expect`` sees the token like on Linux guests.

Windows has no ``sync``/``poweroff -f`` split: the token is written right before the guest
starts its own shutdown, which is what flushes the disk. The bootstrap therefore waits for
QEMU to exit on its own for a long grace period (``SHUTDOWN_GRACE_SEC``) instead of the
default 30 s. Never shorten that to "speed up" the flow (see CLAUDE.md).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _x

from vmctl import cloud_init, iso, runtime, ssh, state, ui
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> Windows installation complete!"
# Written instead of the success token when a first-logon step failed; the guest shuts down anyway.
BOOTSTRAP_FAILED_TOKEN = "==> Windows installation FAILED"

# How long run_and_expect lets Windows finish its own shutdown after printing the token.
SHUTDOWN_GRACE_SEC = 600

SEED_VOLUME_ID = "VMCTLSEED"
SETUP_SCRIPT_NAME = "vmctl-setup.ps1"
GUEST_LOG_DIR = r"C:\vmctl"

# Public KMS client setup keys (Microsoft "Generic Volume License Keys"): they let Setup skip
# the product-key screen and install the matching edition; they never activate anything.
GENERIC_PRODUCT_KEYS = {
    "Windows 11 Pro": "W269N-WFGWX-YVC9B-4J6C9-T83GX",
    "Windows 11 Home": "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99",
    "Windows 11 Enterprise": "NPPR9-FWDCX-D2C8J-H872K-2YT43",
    "Windows 11 Education": "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2",
    "Windows 10 Pro": "W269N-WFGWX-YVC9B-4J6C9-T83GX",
    "Windows 10 Home": "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99",
    "Windows 10 Enterprise": "NPPR9-FWDCX-D2C8J-H872K-2YT43",
    "Windows 10 Education": "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2",
}

DEFAULT_VIRTIO_ISO = "isos/virtio-win.iso"
DEFAULT_VIRTIO_ISO_URL = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"

# Drive letters WinPE may hand to the CD-ROMs (install ISO, virtio-win, seed): the answer
# file lists every combination so the driver injection never depends on enumeration order.
DRIVER_CD_LETTERS = ("D", "E", "F", "G")
DRIVER_DIRS = ("viostor", "NetKVM")

_COMPONENT_ATTRS = (
    'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" '
    'versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"'
)


def windows_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("windows_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid windows_config: expected object")
    return cfg


def windows_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "windows"


def _require_identity(cfg: dict[str, Any]) -> tuple[str, str]:
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "")
    if not username:
        raise VMError("windows_config.username is required")
    if not password:
        raise VMError("windows_config.password is required (plain text: autounattend.xml cannot take a hash)")
    if "\n" in password or "\r" in password:
        raise VMError("windows_config.password cannot contain newlines")
    return username, password


def computer_name(vm_name: str, cfg: dict[str, Any]) -> str:
    """NetBIOS-safe computer name: letters, digits and hyphens, at most 15 characters."""
    raw = str(cfg.get("computer_name") or vm_name)
    cleaned = re.sub(r"[^A-Za-z0-9-]", "-", raw).strip("-")
    if not cleaned:
        raise VMError(f"windows_config.computer_name has no usable characters: {raw!r}")
    return cleaned[:15].rstrip("-").upper()


def edition_family(edition: str) -> str | None:
    """``Pro`` / ``Home`` / ``Enterprise`` / ``Education`` from an image name such as
    ``Windows 11 Pro``, ``Windows 11 Professional`` (UUP dump) or ``Windows 10 Pro N``."""
    words = set(re.findall(r"[a-z]+", edition.lower()))
    for family, aliases in (
        ("Enterprise", {"enterprise"}),
        ("Education", {"education"}),
        ("Home", {"home", "core"}),
        ("Pro", {"pro", "professional"}),
    ):
        if words & aliases:
            return family
    return None


def product_key(cfg: dict[str, Any]) -> str:
    key = str(cfg.get("product_key") or "").strip()
    if key:
        return key
    edition = str(cfg.get("edition") or "Windows 11 Pro").strip()
    generic = GENERIC_PRODUCT_KEYS.get(edition)
    if generic is None:
        family = edition_family(edition)
        major = "10" if "10" in edition else "11"
        generic = GENERIC_PRODUCT_KEYS.get(f"Windows {major} {family}") if family else None
    if generic is None:
        raise VMError(f"No generic product key known for edition {edition!r}: set windows_config.product_key")
    return generic


def _resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey = ssh.resolve_ssh_public_key(vm, ssh_cfg, dry_run=dry_run)
    if pubkey is None or not pubkey.is_file():
        return None
    return pubkey.read_text(encoding="utf-8").strip()


def install_openssh(vm: dict[str, Any]) -> bool:
    cfg = windows_config(vm) or {}
    value = cfg.get("install_openssh")
    if value is None:
        return isinstance(vm.get("ssh_provision"), dict)
    return bool(value)


def _driver_paths_xml(flavor: str) -> str:
    lines: list[str] = []
    key = 1
    for directory in DRIVER_DIRS:
        for letter in DRIVER_CD_LETTERS:
            lines.append(
                f'        <PathAndCredentials wcm:action="add" wcm:keyValue="{key}">\n'
                f"          <Path>{letter}:\\{directory}\\{_x(flavor)}\\amd64</Path>\n"
                f"        </PathAndCredentials>"
            )
            key += 1
    return "\n".join(lines)


def _bypass_xml() -> str:
    checks = ("BypassTPMCheck", "BypassSecureBootCheck", "BypassCPUCheck", "BypassRAMCheck", "BypassStorageCheck")
    lines = ["      <RunSynchronous>"]
    for order, check in enumerate(checks, start=1):
        lines.append(
            '        <RunSynchronousCommand wcm:action="add">\n'
            f"          <Order>{order}</Order>\n"
            f"          <Path>reg add HKLM\\SYSTEM\\Setup\\LabConfig /v {check} /t REG_DWORD /d 1 /f</Path>\n"
            "        </RunSynchronousCommand>"
        )
    lines.append("      </RunSynchronous>")
    return "\n".join(lines)


# Windows 10 silently skips HKLM RunOnce values longer than MAX_PATH (260 chars); FirstLogonCommands
# are stored there, so the launcher must stay short (verified: a 307-char test entry was never run,
# a short one was; Windows 11 has no such limit). Keep this well below the limit.
RUNONCE_MAX_COMMAND_LENGTH = 260


def first_logon_command() -> str:
    """Find the seed CD by its script (any of D..G) and run it: one short cmd.exe line."""
    letters = " ".join(DRIVER_CD_LETTERS)
    command = (
        f"cmd.exe /c for %d in ({letters}) do if exist %d:\\{SETUP_SCRIPT_NAME} "
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\\{SETUP_SCRIPT_NAME}"
    )
    assert len(command) < RUNONCE_MAX_COMMAND_LENGTH, len(command)
    return command


def render_autounattend(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = windows_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define windows_config")
    username, password = _require_identity(cfg)

    realname = str(cfg.get("realname") or username).strip()
    organization = str(cfg.get("organization") or "Lab").strip()
    edition = str(cfg.get("edition") or "Windows 11 Pro").strip()
    image_index = cfg.get("image_index")
    language = str(cfg.get("language") or "en-US").strip()
    input_locale = str(cfg.get("input_locale") or language).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    flavor = str(cfg.get("driver_flavor") or "w11").strip()
    bypass = bool(cfg.get("bypass_requirements", True))
    auto_logon = bool(cfg.get("auto_logon", True))
    name = computer_name(vm_name, cfg)
    key = product_key(cfg)

    international_pe = f"""    <component name="Microsoft-Windows-International-Core-WinPE" {_COMPONENT_ATTRS}>
      <SetupUILanguage>
        <UILanguage>{_x(language)}</UILanguage>
      </SetupUILanguage>
      <InputLocale>{_x(input_locale)}</InputLocale>
      <SystemLocale>{_x(language)}</SystemLocale>
      <UILanguage>{_x(language)}</UILanguage>
      <UserLocale>{_x(language)}</UserLocale>
    </component>"""

    setup_pe = f"""    <component name="Microsoft-Windows-Setup" {_COMPONENT_ATTRS}>
      <UserData>
        <AcceptEula>true</AcceptEula>
        <FullName>{_x(realname)}</FullName>
        <Organization>{_x(organization)}</Organization>
        <ProductKey>
          <Key>{_x(key)}</Key>
          <WillShowUI>OnError</WillShowUI>
        </ProductKey>
      </UserData>
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Type>EFI</Type>
              <Size>260</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order>
              <Type>MSR</Type>
              <Size>16</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order>
              <Type>Primary</Type>
              <Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Label>System</Label>
              <Format>FAT32</Format>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order>
              <PartitionID>2</PartitionID>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>3</Order>
              <PartitionID>3</PartitionID>
              <Label>Windows</Label>
              <Format>NTFS</Format>
              <Letter>C</Letter>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>{"/IMAGE/INDEX" if image_index is not None else "/IMAGE/NAME"}</Key>
              <Value>{int(image_index) if image_index is not None else _x(edition)}</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo>
            <DiskID>0</DiskID>
            <PartitionID>3</PartitionID>
          </InstallTo>
          <WillShowUI>OnError</WillShowUI>
        </OSImage>
      </ImageInstall>
{_bypass_xml() if bypass else ""}
    </component>"""

    drivers_pe = f"""    <component name="Microsoft-Windows-PnpCustomizationsWinPE" {_COMPONENT_ATTRS}>
      <DriverPaths>
{_driver_paths_xml(flavor)}
      </DriverPaths>
    </component>"""

    # Runs at the first logon of the administrator (HKLM RunOnce written by Setup). Nothing goes
    # into specialize RunSynchronous on purpose: a non-zero exit there blocks Setup with a modal
    # dialog, and disabling UAC there leaves Windows 11's OOBE (a modern app) on a black screen.
    first_logon = first_logon_command()

    specialize = f"""    <component name="Microsoft-Windows-Shell-Setup" {_COMPONENT_ATTRS}>
      <ComputerName>{_x(name)}</ComputerName>
      <TimeZone>{_x(timezone)}</TimeZone>
    </component>
    <component name="Microsoft-Windows-International-Core" {_COMPONENT_ATTRS}>
      <InputLocale>{_x(input_locale)}</InputLocale>
      <SystemLocale>{_x(language)}</SystemLocale>
      <UILanguage>{_x(language)}</UILanguage>
      <UserLocale>{_x(language)}</UserLocale>
    </component>"""

    auto_logon_xml = ""
    if auto_logon:
        auto_logon_xml = f"""      <AutoLogon>
        <Enabled>true</Enabled>
        <Username>{_x(username)}</Username>
        <Password>
          <Value>{_x(password)}</Value>
          <PlainText>true</PlainText>
        </Password>
        <LogonCount>999</LogonCount>
      </AutoLogon>
"""

    # International-Core must be declared in oobeSystem too: without it (and without the
    # deprecated Skip*OOBE flags) Windows 10 stops at the region/keyboard OOBE pages.
    oobe = f"""    <component name="Microsoft-Windows-International-Core" {_COMPONENT_ATTRS}>
      <InputLocale>{_x(input_locale)}</InputLocale>
      <SystemLocale>{_x(language)}</SystemLocale>
      <UILanguage>{_x(language)}</UILanguage>
      <UserLocale>{_x(language)}</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" {_COMPONENT_ATTRS}>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Name>{_x(username)}</Name>
            <DisplayName>{_x(realname)}</DisplayName>
            <Group>Administrators</Group>
            <Password>
              <Value>{_x(password)}</Value>
              <PlainText>true</PlainText>
            </Password>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
{auto_logon_xml}      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Description>vmctl first-logon setup</Description>
          <CommandLine>{_x(first_logon)}</CommandLine>
          <RequiresUserInput>false</RequiresUserInput>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- autounattend.xml generated by vmctl for {_x(vm_name)}: do not edit, change windows_config in the profile -->
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="windowsPE">
{international_pe}
{setup_pe}
{drivers_pe}
  </settings>
  <settings pass="specialize">
{specialize}
  </settings>
  <settings pass="oobeSystem">
{oobe}
  </settings>
</unattend>
"""


def _ps_sq(value: str) -> str:
    """Quote *value* as a PowerShell single-quoted string literal."""
    return "'" + value.replace("'", "''") + "'"


def render_setup_script(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> str:
    """Render ``vmctl-setup.ps1``: runs once at the first (auto)logon of the administrator.

    Every step runs under ``$ErrorActionPreference = 'Stop'`` inside ``Invoke-Step``, which
    records failures instead of aborting; native installers are checked through their exit
    codes. The success token is written only when no step failed, otherwise the FAILED token
    goes out and the guest still shuts down, so QEMU exits and the host reports the error at
    once instead of waiting for the timeout.
    """
    cfg = windows_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define windows_config")
    _require_identity(cfg)
    guest_tools = bool(cfg.get("install_guest_tools", True))
    setup_commands: list[str] = [str(c) for c in (cfg.get("setup_commands") or [])]
    pubkey = _resolve_ssh_pubkey(vm, dry_run=dry_run) if install_openssh(vm) else None

    guest_tools_block = ""
    if guest_tools:
        guest_tools_block = """Invoke-Step 'VirtIO guest tools' {
    $tools = Find-MediaFile 'virtio-win-guest-tools.exe'
    if (-not $tools) { throw 'virtio-win-guest-tools.exe not found on any drive' }
    Log "Installing VirtIO guest tools from $tools"
    $proc = Start-Process -FilePath $tools -ArgumentList '/install', '/quiet', '/norestart' -Wait -PassThru
    # 3010 / 1641 = success, reboot required (we reboot anyway)
    if ($proc.ExitCode -notin @(0, 3010, 1641)) { throw "installer exited with code $($proc.ExitCode)" }
}
"""

    openssh_block = ""
    if install_openssh(vm):
        key_block = ""
        if pubkey:
            key_block = f"""Invoke-Step 'SSH authorized key' {{
    # SIDs, not names: the Administrators group is localized on non-English editions.
    $authorized = 'C:\\ProgramData\\ssh\\administrators_authorized_keys'
    Set-Content -Path $authorized -Value {_ps_sq(pubkey)} -Encoding Ascii
    icacls.exe $authorized /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {{ throw "icacls exited with code $LASTEXITCODE" }}
    Log "Authorized key installed for administrators"
}}
"""
        openssh_block = f"""Invoke-Step 'OpenSSH Server' {{
    Log "Installing OpenSSH Server"
    $installed = $false
    for ($attempt = 1; $attempt -le 8; $attempt++) {{
        $cap = Get-WindowsCapability -Online | Where-Object {{ $_.Name -like 'OpenSSH.Server*' }} | Select-Object -First 1
        if ($cap -and $cap.State -eq 'Installed') {{ $installed = $true; break }}
        try {{
            if ($cap) {{
                Add-WindowsCapability -Online -Name $cap.Name | Out-Null
                # re-check right away: a success on the last attempt must count too
                if ((Get-WindowsCapability -Online -Name $cap.Name).State -eq 'Installed') {{ $installed = $true; break }}
            }}
        }} catch {{
            Log "OpenSSH install attempt $attempt failed: $($_.Exception.Message)"
        }}
        Start-Sleep -Seconds 20
    }}
    if (-not $installed) {{ throw 'OpenSSH.Server capability could not be installed (Windows Update unreachable?)' }}
    Set-Service -Name sshd -StartupType Automatic
    Start-Service -Name sshd
    if (-not (Get-NetFirewallRule -Name 'vmctl-sshd' -ErrorAction SilentlyContinue)) {{
        New-NetFirewallRule -Name 'vmctl-sshd' -DisplayName 'OpenSSH Server (vmctl)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }}
    if ((Get-Service -Name sshd).Status -ne 'Running') {{ throw 'sshd is not running after Start-Service' }}
    Log "OpenSSH Server ready"
}}
{key_block}"""

    command_blocks = []
    for index, command in enumerate(setup_commands, start=1):
        body = "\n".join(f"    {line}" for line in command.splitlines())
        command_blocks.append(
            f"Invoke-Step 'setup command {index}' {{\n"
            "    $global:LASTEXITCODE = 0\n"
            f"{body}\n"
            "    if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { throw \"exit code $LASTEXITCODE\" }\n"
            "}"
        )
    commands_block = "\n".join(command_blocks) if command_blocks else "# (no setup_commands in the profile)"

    return f"""# Windows first-logon setup generated by vmctl for {vm_name}
# Launched from the VMCTLSEED CD-ROM by FirstLogonCommands as the local administrator (elevated).
$ErrorActionPreference = 'Stop'
$LogDir = {_ps_sq(GUEST_LOG_DIR)}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir 'setup.log'
$script:Failures = @()

function Write-Serial([string]$Text) {{
    # COM1 is the QEMU serial console: the host follows these lines in bootstrap-serial.log.
    try {{
        $port = New-Object System.IO.Ports.SerialPort 'COM1', 115200, 'None', 8, 'One'
        $port.Open()
        $port.WriteLine($Text)
        $port.Close()
    }} catch {{ }}
}}

function Log([string]$Message) {{
    $line = "[vmctl-windows] $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Serial $line
}}

function Find-MediaFile([string]$RelativePath) {{
    foreach ($drive in Get-PSDrive -PSProvider FileSystem) {{
        $candidate = Join-Path $drive.Root $RelativePath
        if (Test-Path $candidate) {{ return $candidate }}
    }}
    return $null
}}

# Runs one step; an exception is recorded, not fatal, so the remaining steps still run and
# the script always reaches the completion block below.
function Invoke-Step([string]$Name, [scriptblock]$Body) {{
    Log "Step: $Name"
    try {{
        & $Body
    }} catch {{
        $script:Failures += $Name
        Log "ERROR in step '$Name': $($_.Exception.Message)"
    }}
}}

Log "Setup script started on $env:COMPUTERNAME as $env:USERNAME"
{guest_tools_block}
Invoke-Step 'Power settings' {{
    Log "Disabling hibernation, sleep and display timeout"
    foreach ($pcArgs in @(@('/hibernate', 'off'), @('/change', 'standby-timeout-ac', '0'), @('/change', 'monitor-timeout-ac', '0'))) {{
        $global:LASTEXITCODE = 0
        powercfg.exe @pcArgs | Out-Null
        if ($LASTEXITCODE -ne 0) {{ throw "powercfg $($pcArgs -join ' ') exited with code $LASTEXITCODE" }}
    }}
}}
{openssh_block}
{commands_block}

# Completion: the success token only when every step passed; otherwise the FAILED token, so the
# host fails fast when QEMU exits. Either way the guest's own shutdown flushes the disk.
if ($script:Failures.Count -eq 0) {{
    Set-Content -Path (Join-Path $LogDir 'setup-done.txt') -Value 'done'
    Log "Setup script finished"
    Write-Serial {_ps_sq(BOOTSTRAP_COMPLETE_TOKEN)}
}} else {{
    Log "Setup script FAILED in: $($script:Failures -join ', ')"
    Write-Serial "{BOOTSTRAP_FAILED_TOKEN}: $($script:Failures -join ', ')"
}}
shutdown.exe /s /t 10 /f
"""


def create_windows_seed_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """Pack autounattend.xml + the setup script into the VMCTLSEED ISO (root of a CD-ROM)."""
    return cloud_init.create_iso_with_files(
        windows_artifact_dir(vm),
        {
            "autounattend.xml": render_autounattend(vm_name, vm),
            SETUP_SCRIPT_NAME: render_setup_script(vm_name, vm, dry_run=dry_run),
        },
        dry_run=dry_run,
        volume_id=SEED_VOLUME_ID,
    )


def virtio_iso_spec(vm: dict[str, Any]) -> dict[str, Any]:
    cfg = windows_config(vm) or {}
    spec: dict[str, Any] = {
        "iso": str(cfg.get("virtio_iso") or DEFAULT_VIRTIO_ISO),
        "iso_url": str(cfg.get("virtio_iso_url") or DEFAULT_VIRTIO_ISO_URL),
    }
    return spec


def ensure_virtio_iso(vm: dict[str, Any], dry_run: bool = False) -> Path:
    """The virtio-win driver ISO (viostor/NetKVM for WinPE, guest tools for the first logon)."""
    return iso.ensure_iso(virtio_iso_spec(vm), dry_run=dry_run)


def noprompt_iso_path(iso_path: Path) -> Path:
    return state.ROOT / "isos" / f"{iso_path.stem}-noprompt.iso"


def _iso_volume_id(iso_path: Path) -> str:
    try:
        report = runtime.run_output(["xorriso", "-indev", str(iso_path), "-pvd_info"])
    except Exception:  # noqa: BLE001 - any failure falls back to a generic label
        return "WINDOWS_VMCTL"
    match = re.search(r"^Volume Id\s*:\s*(.+?)\s*$", report, re.MULTILINE)
    return match.group(1)[:32] if match and match.group(1).strip() else "WINDOWS_VMCTL"


def seven_zip_command() -> str:
    """Microsoft ISOs carry their files in UDF only (the ISO 9660 tree holds a README): 7z reads it, xorriso does not."""
    for name in ("7z", "7zz", "7za"):
        if shutil.which(name):
            return name
    raise VMError("Missing 7z (package p7zip-full or 7zip): required to unpack the UDF file system of a Windows ISO")


def noprompt_source_stamp_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".source")


def _source_stamp(iso_path: Path) -> str:
    """Identity of the source ISO the cache was built from: resolved path, size, mtime."""
    st = iso_path.stat()
    return f"{iso_path.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n"


def ensure_noprompt_iso(iso_path: Path, dry_run: bool = False) -> Path:
    """Rebuild the Microsoft ISO with the prompt-free UEFI boot image; cached in isos/.

    The cache is keyed to the *source*, not only to its file name: a replaced windows11.iso, or
    two ISOs with the same name in different directories, trigger a rebuild.
    """
    dest = noprompt_iso_path(iso_path)
    stamp_path = noprompt_source_stamp_path(dest)
    stamp = _source_stamp(iso_path) if iso_path.is_file() else None
    if dest.is_file():
        recorded = stamp_path.read_text(encoding="utf-8") if stamp_path.is_file() else None
        if stamp is None or recorded == stamp:
            ui.print_status("ok", f"Prompt-free install ISO ready: {ui.pretty_path(dest)}")
            return dest
        ui.print_status("warn", f"Cached {ui.pretty_path(dest)} was built from a different source ISO: rebuilding", ok=False)
    runtime.require_command("xorriso")
    seven_zip = seven_zip_command()
    ui.print_header("Rebuild Windows ISO without the boot prompt")
    ui.print_kv("source", ui.pretty_path(iso_path))
    ui.print_kv("target", ui.pretty_path(dest))
    work = dest.with_suffix(".extract")
    partial = dest.with_name(dest.name + ".part")
    if work.exists() and not dry_run:
        shutil.rmtree(work)
    runtime.ensure_parent(dest)
    runtime.run([seven_zip, "x", "-y", f"-o{work}", str(iso_path)], dry_run=dry_run, quiet=True)
    efi_image = "efi/microsoft/boot/efisys_noprompt.bin"
    if not dry_run:
        for path in work.rglob("*"):
            try:
                path.chmod(path.stat().st_mode | 0o200)
            except OSError:
                pass
        if not (work / efi_image).is_file():
            ui.print_status("warn", "efisys_noprompt.bin not found: keeping the prompting efisys.bin", ok=False)
            efi_image = "efi/microsoft/boot/efisys.bin"
        bootfix = work / "boot" / "bootfix.bin"
        if bootfix.is_file():
            bootfix.write_bytes(b"")  # the BIOS-side "press any key" helper
    volume_id = "WINDOWS_VMCTL" if dry_run else _iso_volume_id(iso_path)
    runtime.run(
        [
            "xorriso", "-as", "mkisofs",
            "-iso-level", "3", "-J", "-joliet-long", "-relaxed-filenames",
            "-V", volume_id,
            "-o", str(partial),
            "-b", "boot/etfsboot.com", "-no-emul-boot", "-boot-load-size", "8",
            "-eltorito-alt-boot",
            "-e", efi_image, "-no-emul-boot",
            str(work),
        ],
        dry_run=dry_run,
        quiet=True,
    )
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work, ignore_errors=True)
        if stamp is not None:
            stamp_path.write_text(stamp, encoding="utf-8")
    ui.print_status("ok", f"Prompt-free install ISO: {ui.pretty_path(dest)}")
    return dest


def cdrom_args(iso_path: Path, drive_id: str, bus: str, bootindex: int | None = None) -> list[str]:
    """A SATA CD-ROM on the q35 AHCI controller: visible to WinPE without any extra driver.

    Each AHCI port (``ide.0`` .. ``ide.5`` on q35) takes exactly one unit, so every CD names
    its own port; letting QEMU auto-place a second ``ide-cd`` fails with "bus supports only 1 units".
    """
    device = f"ide-cd,drive={drive_id},bus={bus}"
    if bootindex is not None:
        device += f",bootindex={bootindex}"
    return [
        "-drive", f"id={drive_id},file={iso_path},format=raw,if=none,media=cdrom,readonly=on",
        "-device", device,
    ]


def install_media_args(install_iso: Path, virtio_iso: Path, seed_iso: Path) -> list[str]:
    """Install ISO (boots after the disk), virtio-win drivers, VMCTLSEED answer file."""
    return (
        cdrom_args(install_iso, "wincd0", "ide.0", bootindex=2)
        + cdrom_args(virtio_iso, "wincd1", "ide.1")
        + cdrom_args(seed_iso, "wincd2", "ide.2")
    )
