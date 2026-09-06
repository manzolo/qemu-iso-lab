# Windows 11 e Windows 10 non presidiati

Profili: `windows11-unattended` (SSH 2235) e `windows10-unattended` (SSH 2236). Stessa
tecnica di kvm-lab (`autounattend.xml`) portata su QEMU puro. Tempo tipico: 25-40 minuti.

## 1. Prerequisiti specifici

- La ISO Microsoft. Non esiste un URL stabile: si scarica dal browser
  (https://www.microsoft.com/it-it/software-download/windows11, "ISO multi-edition x64") e si
  mette in `isos/windows11.iso`, oppure si indica il percorso in `local.json`. Per Windows 10
  vale lo stesso con `isos/windows10.iso`.
- `7z` e `xorriso` sull'host (`vmctl setup` li elenca): la ISO Microsoft tiene i file in UDF e
  va ricostruita una volta senza il prompt "Premere un tasto per avviare da CD".
- La ISO dei driver virtio-win: scaricata da fedorapeople al primo uso in `isos/virtio-win.iso`,
  oppure `windows_config.virtio_iso` verso una copia locale.
- `virtiofsd` se vuoi la cartella condivisa (i profili la hanno già).

## 2. Personalizzazione in local.json

```json
"windows11-unattended": {
  "iso": "/percorso/Win11_25H2_Italian_x64_v2.iso",
  "windows_config": {
    "username": "TUO_UTENTE", "password": "TUA_PASSWORD", "realname": "Nome Cognome",
    "edition": "Windows 11 Pro", "language": "it-IT", "input_locale": "it-IT",
    "timezone": "W. Europe Standard Time",
    "virtio_iso": "/percorso/virtio-win-0.1.285.iso"
  },
  "ssh_provision": { "user": "TUO_UTENTE" },
  "shared_dir": { "source": "/percorso/shared", "tag": "shared" }
}
```

`edition` deve essere il nome dell'immagine dentro `install.wim` ("Windows 11 Pro" sulle ISO
Microsoft, "Windows 10 Pro" per la 10). La password è in chiaro: l'answer file non accetta hash.

## 3. Il comando

```bash
vmctl bootstrap-windows windows11-unattended        # --timeout 3600 di default
vmctl attach windows11-unattended                   # in un altro terminale, per guardare
```

## 4. Cosa succede, fase per fase

1. **ISO senza prompt** (una volta sola): `7z x` estrae la ISO, `xorriso -as mkisofs` la
   ricostruisce con `efi/microsoft/boot/efisys_noprompt.bin` come immagine di boot UEFI. Il
   risultato è `isos/<nome>-noprompt.iso`, con un file `.source` che ricorda da quale ISO viene.
2. **Seed** `VMCTLSEED` in `artifacts/<vm>/windows/`: `autounattend.xml` e `vmctl-setup.ps1`.
   Windows Setup cerca l'answer file alla radice di ogni unità rimovibile, quindi il seed è un
   normale CD SATA accanto alla ISO di installazione e a virtio-win.
3. **QEMU**: disco `virtio-blk-pci` con `bootindex=1`, CD di installazione con `bootindex=2`
   (OVMF passa al CD solo finché il disco è vuoto), seriale su stdio, nessun `-no-reboot`
   perché Setup riavvia più volte.
4. **WinPE**: l'answer file inietta i driver `viostor` e `NetKVM` dal CD virtio-win, azzera il
   disco 0 (GPT: EFI 260 MB, MSR 16 MB, Windows), scrive in `HKLM\SYSTEM\Setup\LabConfig` i
   bypass di TPM/Secure Boot/CPU/RAM (solo Windows 11), installa l'edizione.
5. **specialize** dopo il primo riavvio: nome computer e fuso orario. Nulla viene eseguito
   qui di proposito: un comando che fallisce in questa fase blocca Setup con una finestra.
6. **OOBE** dopo il secondo riavvio: utente locale amministratore, autologon, nessuna pagina
   grazie a `HideEULAPage`, `HideLocalAccountScreen`, `HideOnlineAccountScreens`,
   `HideWirelessSetupInOOBE`, `ProtectYourPC=3` e alle impostazioni internazionali dichiarate
   anche nel pass oobeSystem.
7. **Primo logon**: `FirstLogonCommands` lancia una sola riga corta (sotto i 260 caratteri,
   Windows 10 ignora in silenzio i `RunOnce` più lunghi):

        cmd.exe /c for %d in (D E F G) do if exist %d:\vmctl-setup.ps1 powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\vmctl-setup.ps1

8. **vmctl-setup.ps1** scrive ogni passo in `C:\vmctl\setup.log` e su COM1, che è il
   seriale visto dall'host. Sul terminale compaiono nell'ordine:

        [vmctl-windows] Setup script started on WIN11-LAB as <utente>
        [vmctl-windows] Step: VirtIO guest tools
        [vmctl-windows] Step: Power settings
        [vmctl-windows] Step: OpenSSH Server            (Add-WindowsCapability, con tentativi)
        [vmctl-windows] Step: SSH authorized key        (administrators_authorized_keys, ACL via SID)
        [vmctl-windows] Step: virtiofs share (WinFSP)   (solo con shared_dir: WinFSP, VirtioFsSvc, collegamento sul desktop)
        [vmctl-windows] Step: setup command 1..N        (i setup_commands del profilo, PowerShell)
        [vmctl-windows] Setup script finished
        ==> Windows installation complete!

    Se uno step fallisce, al posto del token arriva `==> Windows installation FAILED: <step>` e
    il guest si spegne comunque: il bootstrap termina subito con l'errore.

9. **Spegnimento** (`shutdown /s`): l'host aspetta fino a 10 minuti che QEMU esca da solo.
10. **Post-install**: la VM riparte headless, `vmctl` attende SSH (`exit 0` come sonda, il
    login shell è `cmd.exe`) ed esegue i `post_install_run` del profilo: `ver`, versione e stato
    di `sshd`, il contenuto di `C:\vmctl\setup.log`, le unità presenti.

## 5. Verifica

```bash
vmctl shell windows11-unattended                    # cmd.exe via OpenSSH
ver
powershell -NoProfile -Command "(Get-Service sshd).Status; Get-PSDrive -PSProvider FileSystem"
dir Z:\                                             # la cartella condivisa
```

## 6. Uso quotidiano e note

- `vmctl stop` usa il pulsante ACPI e aspetta fino a 300 secondi (`acpi_poweroff_grace_sec`):
  il primo spegnimento dopo il bootstrap completa le "operazioni sulle funzionalità" di
  OpenSSH e può superare il minuto. Non forzare.
- I `setup_commands` dei profili abilitano Desktop remoto nel guest; la porta 3389 non è
  inoltrata sull'host, aggiungila se ti serve.
- Cambiando `windows_config` basta rilanciare il bootstrap: la ISO senza prompt è in cache, il
  seed viene rigenerato.

## 7. Rifarlo a mano

I file generati sono in `artifacts/<vm>/windows/` (`autounattend.xml`, `vmctl-setup.ps1`,
`seed.iso`). Qualunque QEMU o libvirt che avvii la ISO senza prompt con quel seed e la ISO
virtio-win come CD aggiuntivi riproduce la stessa installazione; il seriale (COM1) è
facoltativo, serve solo a leggere i progressi.
