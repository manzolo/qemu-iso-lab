# Lab di hardening SSH: irrobustire un server e vederlo difendersi

Due server Ubuntu 24.04 su un segmento isolato: un server da irrobustire e un client da cui
sondarlo. È tutto difensivo — configurazione di SSH, fail2ban e port knocking — provato contro
una macchina tua, su una rete che non raggiunge nient'altro. Portato dal ssh-lab di qlab:
`labuser` è diventato l'utente del profilo e la LAN è passata a 172.20.2.0/24.

| VM | Ruolo | Segmento `ssh-lan` | SSH dall'host |
|---|---|---|---|
| `ssh-lab-server` | il bersaglio: sshd, fail2ban, knockd | 172.20.2.1 | `vmctl shell ssh-lab-server` (127.0.0.1:2361) |
| `ssh-lab-client` | la sonda: ssh-client, nmap, knock, sshpass | 172.20.2.2 | `vmctl shell ssh-lab-client` (127.0.0.1:2362) |

Ogni VM ha una scheda NAT per l'host (è così che vmctl le raggiunge) e `ssh-lan`, il segmento
condiviso dalle due VM. **fail2ban ignora la rete NAT (10.0.2.0/24)** e la catena del port knock
filtra solo il segmento: niente di quello che fai in questo lab può chiudere fuori vmctl — o te —
da una delle due macchine.

## Avvio

```bash
vmctl group install ssh-lab       # due immagini cloud, un minuto l'una dopo il download; poi lo stack si accende
vmctl group status ssh-lab
vmctl group map ssh-lab --open    # la mappa, la tabella degli accessi e gli esercizi
```

Apri due terminali: `vmctl shell ssh-lab-server` e `vmctl shell ssh-lab-client`. Un comando senza
sessione: `vmctl shell ssh-lab-server -- sudo fail2ban-client status sshd`.

## Esercizio 1: anatomia di SSH

Sul server, guarda cosa offre sshd:

```bash
systemctl status ssh
grep -vE '^#|^$' /etc/ssh/sshd_config      # Port, PermitRootLogin, PasswordAuthentication, PubkeyAuthentication
```

Sul client, verifica che il segmento raggiunga sshd (una semplice apertura TCP sulla porta 22):

```bash
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' && echo raggiungibile
```

## Esercizio 2: autenticazione a chiave

Sul client, crea una chiave moderna e il classico più pesante, e leggine il tipo:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lab_key -N ''
ssh-keygen -l -f ~/.ssh/lab_key.pub        # l'impronta dice ED25519
ssh-keygen -t rsa -b 4096 -f ~/.ssh/lab_rsa -N '' && ssh-keygen -l -f ~/.ssh/lab_rsa.pub
rm -f ~/.ssh/lab_key* ~/.ssh/lab_rsa*      # lascia la home come l'hai trovata
```

ed25519 è il default moderno: chiavi corte, operazioni veloci. RSA a 4096 bit è il classico compatibile.

## Esercizio 3: hardening di sshd_config

Irrobustisci il server, validando sempre prima di ricaricare perché un refuso non lasci sshd
incapace di partire, poi rimetti il file a posto:

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config
sudo sshd -t && sudo systemctl reload ssh
grep -E '^(PermitRootLogin|MaxAuthTries)' /etc/ssh/sshd_config
sudo cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config && sudo systemctl reload ssh
```

`PubkeyAuthentication` resta attivo e la chiave del progetto resta in `authorized_keys`, così
vmctl conserva l'accesso per tutto l'esercizio.

## Esercizio 4: fail2ban banna un client rumoroso

fail2ban guarda il log di autenticazione e banna un indirizzo dopo qualche fallimento. È limitato
al segmento del lab: il lato NAT è in `ignoreip`, quindi non tocca mai l'host.

```bash
# Sul server, guarda la jail:
sudo fail2ban-client status sshd
# Sul client, qualche password sbagliata sul segmento:
for i in 1 2 3 4; do sshpass -p nope ssh -o StrictHostKeyChecking=no \
  -o PreferredAuthentications=password -o PubkeyAuthentication=no nobody@172.20.2.1 true; done
# Di nuovo sul server, il client è ora nella lista bannati:
sudo fail2ban-client status sshd
# Toglilo (sempre, così l'esercizio dopo può connettersi):
sudo fail2ban-client set sshd unbanip 172.20.2.2
```

## Esercizio 5: port knocking

SSH dal segmento parte chiuso: una catena iptables `KNOCKD_SSH` che lo scarta. knockd lo apre per
una sorgente solo dopo la sequenza 7000, 8000, 9000, e la sequenza inversa lo richiude. (vmctl
raggiunge il server dalla NAT, fuori da questa catena, quindi non è mai filtrato.)

```bash
# Sul server:
sudo iptables -S KNOCKD_SSH                  # DROP di default
# Sul client — chiuso, poi knock, poi aperto, poi chiuso:
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' || echo chiuso
knock 172.20.2.1 7000 8000 9000
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' && echo aperto
knock 172.20.2.1 9000 8000 7000
```

## Esercizio 6: cosa vede una scansione

Guarda il server dal client come farebbe una scansione dall'esterno (apri prima la porta con un
knock), e trova la traccia che lascia sul server:

```bash
# Sul client:
knock 172.20.2.1 7000 8000 9000
nmap -Pn -p 22 172.20.2.1
nmap -Pn -sV -p 22 172.20.2.1               # banner e versione di OpenSSH
# Sul server:
sudo journalctl -u ssh --no-pager -n 20     # i tentativi di connessione dal client
```

## Test

```bash
vmctl group test ssh-lab          # tutti e sei gli script, stack acceso se serve
```

Ogni script lascia il server come l'ha trovato: il test di hardening ripristina `sshd_config`,
quello di fail2ban sbanna il client, quello del knocking richiude la porta.

## Spegnere e pulire

```bash
vmctl group down ssh-lab
vmctl group clean ssh-lab         # cancella i due dischi overlay (chiede prima); l'immagine cloud resta in isos/
```
