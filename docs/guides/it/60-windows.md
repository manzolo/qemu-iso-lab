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

## 8. Windows 7 Ultimate (`windows7-unattended`)

Stesso comando (`vmctl bootstrap-windows windows7-unattended`), stessa tecnica del `Windows7U`
di kvm-lab, con le differenze che Windows 7 impone:

- **BIOS e MBR**: profilo con `firmware.type: bios`, answer file con partizione "System
  Reserved" (100 MB, attiva) + Windows; nessun bypass TPM/CPU (`LabConfig`) perché non serve.
- **Driver**: `viostor` iniettato in WinPE dal CD virtio-win (`driver_flavor: w7`); la rete è
  `e1000e`, nativa su Windows 7, quindi NetKVM non è necessario. Il certificato Red Hat dei
  driver viene importato nel pass specialize (come SYSTEM) da `vmctl-cert.cmd` sul seed, che
  termina sempre con esito 0 (un comando specialize non a zero bloccherebbe Setup con una dialog).
- **Guest agent**: il profilo mantiene `guest_agent: true`. vmctl inietta `vioserial` in
  WinPE e scarica sull'host l'[MSI Fedora dell'agente 101.1.0](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-qemu-ga/qemu-ga-win-101.1.0-1.el7ev/),
  fissato da `windows_config.guest_agent_msi` (`path`, `url`, `sha256`). Il seed lo contiene
  come `vmctl-qga.msi`. Il comando Order 2 in specialize chiama `vmctl-qga.cmd stage`, che
  copia script e MSI in `C:\Windows\Setup\Scripts` ed esce sempre 0.
  `SetupComplete.cmd` lo installa come SYSTEM con `msiexec /i /qn /norestart` a Setup finito,
  quando WMI è disponibile. Il primo logon controlla l'esito MSI e il servizio avviato prima
  del token di completamento; anche gli errori portano allo spegnimento naturale. L'agente è
  l'unico canale remoto su 7 perché SSH non è disponibile. Log: `C:\vmctl-qga.log`,
  `C:\vmctl-qga-msi.log`, `C:\vmctl-qga-exit.txt` e `C:\vmctl\setup.log`.
  La diagnosi dal vivo su Windows 7 RTM (7600, senza SP1) ha mostrato che l'agente 110.0.2 di
  virtio-win 0.1.285 si ferma nel loader per l'assenza di `api-ms-win-core-path-l1-1-0.dll`,
  prima di scrivere il log console o aprire il canale. Il figlio `VIOSerialPort` non richiede
  un INF separato: è un dispositivo raw. `QueryDosDevice` ha trovato
  `\\.\Global\org.qemu.guest_agent.0`, `CreateFile` lo ha aperto senza errori e 101.1.0 ha
  risposto a ping, informazioni OS e indirizzi con lo stesso driver. L'MSI in specialize
  fallisce comunque nella registrazione WMI/VSS: va usato SetupComplete. Questo pacchetto
  serve solo a Windows 7; Windows 10/11 conservano il flusso attuale.
- **Edizione**: `image_index: 4` (Ultimate sul media multi-edizione standard) e chiave KMS
  generica `33PXH-7Y6KF-2VJC9-XBBR8-HVTHH`; installa, non attiva.
- **OOBE**: `SkipMachineOOBE`/`SkipUserOOBE` (qui funzionano), `NetworkLocation=Work`,
  autologon.
- **Primo logon**: PowerShell 2.0, non elevato (UAC resta attivo): lo script controlla il servizio
  dell'agente, esegue i `setup_commands`, scrive `==> Windows installation complete!` su COM1 e spegne. **Niente
  OpenSSH** su Windows 7, quindi nessun post-install SSH: `check-vms` considera passata la sola
  installazione, e `vmctl shell` non è disponibile. Gli altri guest tools virtio/SPICE restano un
  passo manuale con pacchetti compatibili con Windows 7; niente cartella condivisa virtiofs
  (WinFSP non esiste per 7).
- **ISO**: `isos/windows7.iso` o il percorso in `local.json`; la ISO senza prompt viene
  ricostruita una volta come per 10 e 11, con due differenze imposte dal loader BIOS di Windows 7:
  `boot/bootfix.bin` viene cancellato, non svuotato (`etfsboot.com` si blocca su "Booting from
  DVD/CD..." con un file vuoto), e xorriso mantiene i nomi ISO 9660 esatti (`-D -N -d`: `CDBOOT`
  cerca `BOOTMGR`, non `BOOTMGR.;1`). Entrambe verificate dal vivo; il timbro della cache cambia
  con loro.

Una nuova installazione è stata verificata con il pacchetto fissato su Windows 7 RTM:
l'MSI da SetupComplete è uscito con 0, `QEMU-GA` era avviato prima del token di completamento
e ping, informazioni OS e indirizzi funzionavano dopo l'avvio del disco installato.
Per ripetere la verifica (il primo comando elimina il guest Windows 7 esistente):

```sh
./bin/vmctl clean windows7-unattended
./bin/vmctl bootstrap-windows windows7-unattended --timeout 3600
./bin/vmctl start windows7-unattended --headless --background
# Attendere l'avvio di Windows, poi:
./bin/vmctl agent windows7-unattended ping
./bin/vmctl agent windows7-unattended
./bin/vmctl stop windows7-unattended
```

Il clean è necessario: un disco già installato precede il CD nel boot, quindi bootstrap su
un Windows esistente non riesegue Setup. L'avviso sul driver USB xHCI (`VEN_1B36&DEV_000D`)
è separato dall'agente. Questa prova non stabilisce la compatibilità delle firme SHA-2 dei
driver su altri media Windows 7.
