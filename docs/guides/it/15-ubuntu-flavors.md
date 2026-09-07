# I flavor desktop di Ubuntu (Lubuntu, Kubuntu, Xubuntu, MATE, Budgie)

Profili: `lubuntu-24.04` (SSH 2240), `kubuntu-24.04` (2241), `xubuntu-24.04` (2242),
`ubuntu-mate-24.04` (2243), `ubuntu-budgie-24.04` (2244). Una ricetta sola, cinque desktop:
la famiglia dei flavor di kvm-lab su QEMU puro. Tempo tipico: 30-40 minuti ciascuno.

## 1. L'idea

Non esiste un installer per flavor. Tutti e cinque si installano dalla **stessa** ISO di
Ubuntu Server 24.04.4 con `bootstrap-unattended`, e il desktop lo sceglie una riga
dell'answer file: il metapacchetto in `autoinstall.packages`. Per flavor cambiano solo

- il metapacchetto (`lubuntu-desktop`, `kubuntu-desktop`, `xubuntu-desktop`,
  `ubuntu-mate-desktop`, `ubuntu-budgie-desktop`),
- il display manager che porta con sé (SDDM per Lubuntu e Kubuntu, LightDM per gli altri tre),
- il nome della sessione richiesta nel file di autologin (`Lubuntu`, `plasma`, `xubuntu`,
  `mate`, `ubuntu-budgie-desktop`).

Così la ISO si scarica una volta sola e vale per tutti, e aggiungere un sesto flavor è un
profilo, non codice.

## 2. Il comando

```bash
vmctl bootstrap-unattended kubuntu-24.04
vmctl attach kubuntu-24.04             # da un altro terminale, per guardare l'installer
```

La maggior parte del tempo se ne va nello scaricare il metapacchetto: la ISO server non
contiene desktop, i pacchetti arrivano dall'archivio attraverso la NIC slirp.

## 3. Cosa imposta il profilo

```json
"autoinstall": { "packages": ["kubuntu-desktop", "spice-vdagent", "qemu-guest-agent"] },
"cloud_init": {
  "write_files": [
    { "path": "/etc/sudoers.d/90-{{user}}-nopasswd", "content": "{{user}} ALL=(ALL) NOPASSWD:ALL\n" },
    { "path": "/etc/sddm.conf.d/vmctl-autologin.conf", "content": "[Autologin]\nUser={{user}}\nSession=plasma\n" }
  ],
  "runcmd": ["systemctl set-default graphical.target", "systemctl enable sddm",
             "systemctl enable --now serial-getty@ttyS0.service"]
}
```

Il getty su `ttyS0` è ciò che rende `vmctl console kubuntu-24.04` un prompt di login mentre
la VM gira in background; vedi la guida 05.

## 4. Verifica

```bash
vmctl shell kubuntu-24.04
systemctl get-default                  # graphical.target
systemctl is-enabled sddm              # enabled
dpkg-query -W -f='${Status}\n' kubuntu-desktop
```

Il post-install esegue esattamente questi tre comandi: una riga verde in `check-vms` vuol
già dire desktop installato e display manager abilitato.

## 5. Un'altra versione, un altro flavor

Copia un profilo e cambia tre campi: `iso`/`iso_url` (una qualsiasi
`ubuntu-<versione>-live-server-amd64.iso`), il metapacchetto e la sessione. 22.04 e 26.04
funzionano allo stesso modo; kvm-lab tiene tutte e tre le versioni dei cinque flavor, qui
seguiamo la LTS corrente per non allungare troppo la matrice di validazione. Le varianti
personali vanno in `local.json`, mai in un profilo tracciato.

Il post-install ora fallisce se graphical.target non è il target predefinito, il metapacchetto desktop non è installato, il display manager non è attivo o manca una sessione grafica locale attiva dell’utente configurato. La sola schermata di login non dimostra che l’accesso automatico sia riuscito. Il controllo attende circa due minuti per l’avvio della sessione; questa modifica non è stata verificata dal vivo.
