# openSUSE Tumbleweed con AutoYaST

Profilo: `opensuse-tumbleweed-autoyast` (SSH 2247). La ricetta Tumbleweed di kvm-lab su QEMU
puro, con il comando `bootstrap-autoyast`. Tempo tipico: 30-45 minuti, quasi tutti spesi a
scaricare pacchetti.

## 1. Prerequisiti specifici

Oltre ai prerequisiti comuni della guida 00 serve solo `xorriso`, per l'immagine di seed. La
ISO è `openSUSE-Tumbleweed-NET-x86_64-Current.iso`, circa 250 MB, e viene scaricata da sola:
avvia l'installer e prende i pacchetti dal repository online `oss`. È un'immagine rolling:
allo stesso indirizzo ogni settimana c'è una build diversa, ed è proprio questo che rende il
profilo un buon collaudo settimanale.

## 2. Il comando

```bash
vmctl bootstrap-autoyast opensuse-tumbleweed-autoyast
vmctl attach opensuse-tumbleweed-autoyast          # per guardare l'installer
```

## 3. Cosa succede, passo per passo

1. **Seed**: il profilo genera un file XML AutoYaST dentro un'immagine etichettata
   `AUTOINST`, collegata come **chiavetta USB**.
2. **Boot**: `boot/x86_64/loader/linux` e il relativo initrd vengono estratti dalla ISO e
   avviati con `install=<repo> autoyast=usb:///autoinst.xml ifcfg=*=dhcp netsetup=dhcp
   textmode=1` e la console seriale. La ISO dell'installer è l'**unico CD-ROM**, ed è SATA:
   linuxrc cerca il repository solo in `/dev/sr*`, quindi un CD virtio sarebbe invisibile,
   mentre un *secondo* CD manda YaST a chiedere quale lettore contenga il disco 1 e
   l'installazione si ferma lì.
3. **Installazione**: YaST partiziona il disco (GPT, 512 MB EFI + root btrfs), installa i
   pattern (`enhanced_base`, `gnome`, `kvm_server`) e i pacchetti del profilo.
4. **Script chroot**: dentro il sistema installato crea l'utente e il suo gruppo, il file di
   sudo senza password, la chiave SSH autorizzata, l'autologin di GDM, abilita i servizi
   (`NetworkManager`, `sshd`, il guest agent, il display manager), imposta il target
   grafico, mette un getty su `ttyS0` e apre SSH in firewalld. Poi `sync`,
   `blockdev --flushbufs`, e solo a quel punto il token di completamento
   `==> AutoYaST install complete!` sulla seriale.

   Due di questi passi esistono per quello che succede senza di loro. Il firewalld di
   openSUSE apre solo `dhcpv6-client`, quindi sshd ascolta, la porta inoltrata si connette e
   la sessione muore allo scambio del banner. E il profilo disattiva la **seconda fase di
   YaST** (`second_stage: false`): girerebbe su tty1 al primo avvio e, poiché l'host chiude
   l'installazione al riavvio del guest, annuncerebbe "The previous installation has failed.
   Would you like it to continue?" a chi apre la VM. Disattivata, è lo script qui sopra a
   configurare il sistema e il primo avvio va diretto a GNOME.
5. **Riavvio**: YaST riavvia, QEMU è stato lanciato con `-no-reboot` ed esce da solo. L'host
   riavvia la VM installata headless e fa il post-install via SSH.

L'ordine del punto 4 è la regola d'oro del progetto: prima il flush, poi il token, e per
ultimo lo spegnimento del guest. Il token non va mai stampato prima.

## 4. Personalizzazione

```json
"autoyast_config": {
  "username": "TUO_UTENTE", "password_hash": "$6$...",
  "keyboard_layout": "italian", "language": "it_IT", "timezone": "Europe/Rome",
  "patterns": ["enhanced_base", "kde"], "packages": ["openssh", "vim"],
  "enable_services": ["NetworkManager", "sshd"],
  "chroot_commands": ["zypper --non-interactive install htop"]
}
```

`install_repo` è la sorgente di installazione sulla riga del kernel; con un DVD completo come
`iso` si può togliere, ma allora il DVD deve essere l'unico CD nella macchina. `patterns` è
dove si cambia desktop: `gnome`, `kde`, `xfce`. I `chroot_commands` finiscono in
coda allo script chroot prima del flush, quindi girano dentro il sistema installato con la
rete non ancora configurata: usa `zypper` solo per pacchetti già presenti sul DVD.

## 5. Verifica

```bash
vmctl shell opensuse-tumbleweed-autoyast
cat /etc/os-release
systemctl get-default          # graphical.target
systemctl is-active sshd
```

## 6. Farlo a mano

Il profilo generato è `artifacts/<vm>/autoyast/autoinst.xml` e la seed ISO gli sta accanto.
Qualsiasi QEMU o libvirt che avvii il kernel del DVD con `autoyast=cd:///autoinst.xml` e i due
CD riproduce la stessa installazione; validare l'XML contro lo schema RELAX NG di YaST
(`xmllint --relaxng .../profile.rng`) è il modo più rapido per capire perché un profilo viene
rifiutato.
