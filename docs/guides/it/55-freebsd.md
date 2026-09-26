# FreeBSD unattended

`freebsd` installa la disc1 pubblica di FreeBSD 14.3 su BIOS/pc con disco
virtio UFS. Servono `xorriso` e `growisofs` sull'host. La ricetta crea `lab`/`lab`,
sudo, la chiave SSH del progetto e il login seriale. SSH usa `127.0.0.1:2271`.

```sh
vmctl bootstrap-freebsd freebsd --timeout 1800
vmctl shell freebsd
vmctl stop freebsd
vmctl check-vms freebsd --clean-first --timeout 1800 --report --document
```

L'installer ha un timeout; gli errori riportano il token FAILED e il log seriale.
Il token di completamento segue sync e smontaggio UFS; lo spegnimento è naturale.
Il guest installato viene verificato con `pgrep`, `freebsd-version`, SSH e sudo.
Non viene installato un desktop. Vedi le [note del flow](../../UNATTENDED.md#freebsd-disc1-bootstrap-freebsd)
e gli [esiti dal vivo](../../PROFILE_TODO.md). Resta disponibile il profilo manuale `freebsd-installer`.
