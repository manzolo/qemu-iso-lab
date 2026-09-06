# AlmaLinux, Rocky, Fedora e Silverblue via kickstart

Profili: `almalinux-server` (SSH 2229, dalla ISO minimal), `rocky9` (2245, la stessa ricetta
su Rocky Linux 9), `fedora-niri-dms-local` (2233, dalla netinst Everything più repository
online) e `fedora-silverblue` (2246, quello immutabile). Flusso `bootstrap-kickstart` con
Anaconda in modalità testo. Tempo: 10 minuti Alma e Rocky, 20-30 Fedora, 25-35 Silverblue.

## 1. Prerequisiti

- ISO `AlmaLinux-10.x-x86_64-minimal.iso` oppure `Fedora-Everything-netinst-x86_64-NN.iso`
  (scaricate da `vmctl`).
- `xorriso`, OVMF. In `local.json`: `kickstart_config.username`, `password_hash` o
  `password`, `ssh_provision.user`.

## 2. Il comando

```bash
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-kickstart fedora-niri-dms-local
```

## 3. Cosa succede

1. `ks.cfg` viene generato in `artifacts/<vm>/kickstart/` e impacchettato in una seed ISO con
   etichetta `KS_CFG`, attaccata come CD virtio.
2. Boot diretto con `vmlinuz` e `initrd.img` estratti dalla ISO e riga kernel:

        inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline inst.repo=<cdrom|URL> console=ttyS0,115200

   `inst.repo` è `cdrom` per Alma (pacchetti dalla ISO) e l'URL del repository Fedora per il
   profilo niri (`kickstart_config.inst_repo`), che usa anche `%packages --ignoremissing`.
3. Nessun input sul seriale: Anaconda in `inst.cmdline` non fa domande, se manca qualcosa al
   kickstart si ferma con un errore invece di chiedere. Anche qui il locale della console
   deve restare ASCII.
4. `%post` nel target: chiave SSH, sudoers, SELinux permissive dove previsto, i
   `post_commands` del profilo (per Fedora: COPR `avengemedia/danklinux` per quickshell,
   `dnf upgrade` perché quickshell è compilato contro Qt 6.11 di `updates`, greetd), quindi:

        sync; blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Kickstart install complete!"
        poweroff -f

5. Post-install via SSH con i `post_install_run` del profilo.

## 4. Verifica

```bash
vmctl shell almalinux-server
cat /etc/os-release | head -2; sudo dnf -q repolist
```

## 5. Rifarlo a mano

Al menu di boot della ISO aggiungi la riga kernel sopra puntando `inst.ks=` al tuo `ks.cfg`
(file su una chiavetta con etichetta, oppure `inst.ks=http://...`). Senza `inst.cmdline`
Anaconda mostra il menu testuale e chiede conferma dove il kickstart è incompleto.

## 6. Rocky Linux 9

`rocky9` è `almalinux-server` con un'altra ISO: stesso kickstart, sorgente di installazione
sul supporto (`inst.repo=cdrom`), `@^minimal-environment`, SELinux enforcing.

```bash
vmctl bootstrap-kickstart rocky9
vmctl shell rocky9 -- cat /etc/rocky-release
```

## 7. Fedora Silverblue (immutabile)

`fedora-silverblue` usa lo stesso comando, ma il profilo porta un blocco
`kickstart_config.ostree` e questo cambia il lavoro di Anaconda: sparisce la transazione
`%packages` e al suo posto compare una riga `ostreesetup`, quindi il sistema è un deployment
dell'albero ostree che la ISO si porta dietro (`file:///ostree/repo`).

```json
"ostree": { "osname": "fedora", "remote": "fedora", "url": "file:///ostree/repo",
            "ref": "fedora/44/x86_64/silverblue", "ref_match": "silverblue" }
```

Il ref contiene la versione di Fedora, perciò `vmctl` lo legge da `refs/heads` **dentro la
ISO** al momento del bootstrap e ricade sul valore del profilo solo se non ci riesce (dry
run, xorriso assente). Passare alla release successiva è questione di cambiare `iso`/`iso_url`;
la riga `[ok] ostree ref: fedora/NN/x86_64/silverblue` sul terminale dice quale albero è
stato usato.

Due conseguenze dell'immutabilità:

- `%post` gira dentro il deployment: può scrivere configurazione (autologin GDM, servizi) ma
  non può installare pacchetti.
- Il software in più si layera dopo e ha effetto al riavvio successivo. Il post-install lancia
  `rpm-ostree install --idempotent --allow-inactive qemu-guest-agent spice-vdagent`;
  `rpm-ostree status` mostra il deployment in attesa.

```bash
vmctl shell fedora-silverblue
rpm-ostree status
systemctl get-default
```
