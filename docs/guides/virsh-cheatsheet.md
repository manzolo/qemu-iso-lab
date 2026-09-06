# virsh: cheat sheet

Bignami dei comandi libvirt più usati nel lab, ereditato da kvm-lab. Connessione
`qemu:///system` (esportabile una volta con `export LIBVIRT_DEFAULT_URI=qemu:///system`, altrimenti
`virsh -c qemu:///system ...`). `<VM>` è il nome del dominio, per esempio `Windows10` o il
profilo esportato con `vmctl export-libvirt`.

## 1. Inventario e stato

```bash
virsh list --all                 # tutte le VM (accese e spente) con stato
virsh list --state-running       # solo quelle in esecuzione
virsh domstate <VM>              # running / shut off / paused
virsh dominfo <VM>               # CPU, RAM, autostart, persistenza, UUID
virsh domid <VM>                 # id numerico (solo se accesa)
virsh domifaddr <VM>             # indirizzi IP (richiede guest-agent o lease DHCP)
```

## 2. Avvio e spegnimento

```bash
virsh start <VM>                 # avvia
virsh shutdown <VM>              # spegnimento ordinato (ACPI): serve un SO che risponde
virsh reboot <VM>                # riavvio ordinato
virsh destroy <VM>               # spegnimento forzato (stacca la spina)
virsh suspend <VM>               # pausa
virsh resume <VM>                # riprendi
virsh autostart <VM>             # avvio automatico al boot dell'host
virsh autostart --disable <VM>
```

## 3. Console e schermo

```bash
virt-viewer <VM>                 # finestra grafica SPICE/VNC (pacchetto virt-viewer)
virsh console <VM>               # console seriale testuale, si esce con Ctrl+]
virsh screenshot <VM> shot.ppm   # cattura lo schermo
convert shot.ppm shot.png        # ImageMagick: da .ppm a .png
virsh send-key <VM> KEY_SPACE    # sveglia il guest (utile prima dello screenshot)
virsh send-key <VM> KEY_ESC
virsh send-key <VM> KEY_LEFTCTRL KEY_LEFTALT KEY_DELETE   # Ctrl-Alt-Canc al guest
```

## 4. Guest agent (dentro la VM)

```bash
virsh qemu-agent-command <VM> '{"execute":"guest-ping"}'         # l'agente risponde? (SO pronto)
virsh qemu-agent-command <VM> '{"execute":"guest-get-osinfo"}'   # nome/versione SO (utile su Windows)
virsh domtime <VM> --sync                                        # sincronizza l'orologio del guest
```

Eseguire un comando nel guest: `guest-exec` con `path` `/bin/sh` e `arg` `["-c","..."]` (su
Windows `path` `cmd.exe`, `arg` `["/c","..."]`), poi `guest-exec-status` con il `pid` restituito
per leggere l'output:

```bash
virsh qemu-agent-command <VM> '{"execute":"guest-exec","arguments":{"path":"/bin/sh","arg":["-c","uptime"],"capture-output":true}}'
virsh qemu-agent-command <VM> '{"execute":"guest-exec-status","arguments":{"pid":1234}}'   # out-data è base64
```

## 5. Dischi e storage pool

```bash
virsh domblklist <VM>            # dischi e ISO collegati, con i percorsi
virsh pool-list --all
virsh pool-info <pool>
virsh pool-refresh <pool>        # dopo aver aggiunto file nella cartella del pool
virsh vol-list <pool>
virsh vol-info --pool <pool> <vol>
qemu-img info disco.qcow2
qemu-img create -f qcow2 -o preallocation=off nuovo.qcow2 20G
virt-cat -a disco.qcow2 /var/log/syslog   # legge un file dentro il disco a VM spenta (libguestfs)
```

`virt-cat` (e `virt-ls`, `guestmount`) sono ottimi per diagnosticare un'installazione fallita
senza avviare la VM. Pool del lab kvm-lab: `hdd`, `shared`, `Linux`, `Windows`, `addons`,
`floppy`, `Utility`, `nvram`.

## 6. Rete

```bash
virsh net-list --all             # reti virtuali
virsh net-info default
virsh net-dhcp-leases default    # lease DHCP assegnati dalla rete NAT
virsh domiflist <VM>             # interfacce della VM: modello, MAC, bridge/rete
virsh net-dumpxml lab-lan        # la LAN isolata del laboratorio di rete
virsh net-start lab-lan
virsh net-autostart lab-lan
```

## 7. Snapshot

```bash
virsh snapshot-create-as <VM> nome "descrizione"   # disco (+ RAM se accesa)
virsh snapshot-list <VM>
virsh snapshot-revert <VM> nome
virsh snapshot-delete <VM> nome
```

## 8. Definizione e XML

```bash
virsh dumpxml <VM> > vm.xml      # esporta la configurazione
virsh edit <VM>                  # modifica con validazione
virsh define vm.xml              # crea o aggiorna da file
virsh dominfo <VM> | grep Persistent   # persistente o transitoria
```

## 9. Eliminare una VM in sicurezza

```bash
# 1. cancella SOLO il disco principale
virsh vol-delete --pool hdd <nome>.qcow2
# 2. rimuovi la definizione (e la NVRAM se UEFI)
virsh undefine <VM> --nvram
```

**MAI `virsh undefine --remove-all-storage`**: cancella tutti i volumi registrati nella
definizione, incluse le ISO nei pool (Windows, Linux, addons...). Perdita irreversibile.

Per una VM esportata da vmctl il comando giusto è `vmctl unexport-libvirt <profilo>`: toglie
la definizione, conserva disco e NVRAM in `artifacts/<vm>/` e non usa mai
`--remove-all-storage`.

## 10. Diagnostica

```bash
virsh dominfo <VM>
virsh domstats <VM>              # cpu / mem / blk / net dal vivo
virsh domblkerror <VM>           # errori I/O sui dischi
virsh domblkstat <VM> vda
sudo virsh dommemstat <VM>       # statistiche memoria (balloon/agent)
journalctl -u libvirtd --since today
cat /var/log/libvirt/qemu/<VM>.log   # log host del processo QEMU della VM
```

## 11. Cartella condivisa (virtiofs)

La VM deve avere il memory backing `memfd` condiviso e il filesystem virtiofs; sul guest
Windows serve WinFSP. È quello che `vmctl export-libvirt` genera per i profili con
`shared_dir`:

```xml
<memoryBacking>
  <source type='memfd'/>
  <access mode='shared'/>
</memoryBacking>
<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs'/>
  <source dir='/percorso/shared/'/>
  <target dir='shared'/>
</filesystem>
```

## 12. vmctl e virsh: chi fa cosa

vmctl installa e provisiona con QEMU puro; libvirt entra in gioco quando si vuole gestire la
VM con virt-manager o farla convivere con le VM legacy del lab. Disco e NVRAM restano quelli di
vmctl (`artifacts/<vm>/disk.qcow2`, `artifacts/<vm>/OVMF_VARS.fd`), riferiti per percorso
assoluto: mai copiati.

| Voglio | vmctl | virsh |
|---|---|---|
| installare da zero | `vmctl bootstrap-* <vm>` | non applicabile |
| passare a libvirt | `vmctl stop <vm>` poi `vmctl export-libvirt <vm>` (`--dry-run` mostra l'XML, `--replace` sovrascrive un dominio spento) | `virsh define artifacts/<vm>/libvirt/<vm>.xml` |
| avviare / fermare | `vmctl start <vm>` (solo se NON esportata) | `virsh start <vm>`, `virsh shutdown <vm>` |
| vedere lo schermo | `vmctl attach <vm>` (VNC headless) | `virt-viewer <vm>` |
| entrare via SSH | `vmctl shell <vm>` (porta hostfwd) | `virsh domifaddr <vm>` poi `ssh utente@IP` sulla porta 22 |
| cartella condivisa | `shared_dir` nel profilo | XML `memoryBacking` + `filesystem` (generati dall'export) |
| laboratorio di rete | `vmctl lab install`, `vmctl lab up` | rete `lab-lan` creata dall'export: `virsh net-list`, host su `192.168.0.254` |
| tornare a vmctl | `virsh shutdown <vm>` fino a `shut off`, poi `vmctl unexport-libvirt <vm>` | `virsh undefine <vm> --nvram` (mai `--remove-all-storage`) |
| eliminare tutto | `vmctl clean <vm>` (disco e artefatti, mai le ISO) | vedi la sezione 9 |

Non avviare mai lo stesso disco con QEMU e con libvirt insieme: `export-libvirt` rifiuta una
VM in esecuzione e `unexport-libvirt` un dominio acceso, ma dopo l'export `vmctl start` non
controlla libvirt. Dettagli in `docs/LIBVIRT.md`.
