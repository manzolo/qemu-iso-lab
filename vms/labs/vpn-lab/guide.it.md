# Lab VPN: WireGuard e OpenVPN tra due macchine

Due server Ubuntu 24.04 su un segmento isolato, con WireGuard e OpenVPN installati e niente di
configurato. I tunnel li costruisci a mano, guardi cosa vede la rete e recinti il server con
iptables. Portato dal vpn-lab di qlab: `labuser` è diventato l'utente del profilo e la LAN è
passata a 172.20.1.0/24 (192.168.100.0/24 è quella di `vmctl link`).

| VM | Ruolo | Segmento `vpn-lan` | SSH dall'host |
|---|---|---|---|
| `vpn-lab-server` | l'estremo VPN: WireGuard 51820/udp, OpenVPN 1194/udp | 172.20.1.1 | `vmctl shell vpn-lab-server` (127.0.0.1:2359) |
| `vpn-lab-client` | si collega sul segmento, poi dentro i tunnel | 172.20.1.2 | `vmctl shell vpn-lab-client` (127.0.0.1:2360) |

Ogni VM ha due schede: una NAT per l'host (SSH, Internet) e `vpn-lan`, un segmento condiviso
solo dalle due VM. I tunnel sono reti private sopra il segmento: WireGuard `10.10.0.0/24`
(server `.1`, client `.2`), OpenVPN `10.20.0.0/24` (server `.1`, client `.2`).

## Avvio

```bash
vmctl group install vpn-lab       # due immagini cloud, un minuto l'una dopo il download; poi lo stack si accende
vmctl group status vpn-lab
vmctl group map vpn-lab --open    # la mappa, la tabella degli accessi e gli esercizi
```

Apri due terminali, uno per macchina: `vmctl shell vpn-lab-server` e `vmctl shell vpn-lab-client`.
Un comando solo, senza sessione: `vmctl shell vpn-lab-server -- sudo wg show`.

## Esercizio 1: WireGuard

**Le chiavi, su entrambe le macchine.** Una coppia ciascuna; viaggiano solo le metà pubbliche:

```bash
wg genkey | tee privatekey | wg pubkey > publickey
cat publickey
```

**Il server** (`/etc/wireguard/wg0.conf`), con la propria chiave privata e la pubblica del client:

```ini
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = <CHIAVE_PRIVATA_SERVER>

[Peer]
PublicKey = <CHIAVE_PUBBLICA_CLIENT>
AllowedIPs = 10.10.0.2/32
```

**Il client**, con la pubblica del server e il suo indirizzo sul segmento come Endpoint:

```ini
[Interface]
Address = 10.10.0.2/24
PrivateKey = <CHIAVE_PRIVATA_CLIENT>

[Peer]
PublicKey = <CHIAVE_PUBBLICA_SERVER>
Endpoint = 172.20.1.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 25
```

`PersistentKeepalive` tiene vivo il tunnel con un pacchetto ogni 25 s. Su, prima il server:

```bash
sudo wg-quick up wg0          # su entrambe
sudo wg show                  # "latest handshake: N seconds ago" vuol dire tunnel in piedi
ping 10.10.0.1                # dal client, dentro il tunnel
sudo wg-quick down wg0        # giù
```

Niente handshake? Controlla le chiavi pubbliche (ogni estremo porta quella *dell'altro*), la porta
(`ListenPort` = `Endpoint`), che il server sia partito per primo e che il segmento funzioni
(`ping 172.20.1.1` dal client, senza VPN).

## Esercizio 2: OpenVPN con chiave statica

L'OpenVPN più semplice: un segreto condiviso, niente certificati. Sul server:

```bash
sudo openvpn --genkey secret /etc/openvpn/static.key
sudo cat /etc/openvpn/static.key          # incollala sul client come /etc/openvpn/static.key
```

`/etc/openvpn/server.conf` sul server, `/etc/openvpn/client.ovpn` sul client:

```
dev tun                              dev tun
ifconfig 10.20.0.1 10.20.0.2         remote 172.20.1.1 1194 udp
secret /etc/openvpn/static.key       ifconfig 10.20.0.2 10.20.0.1
cipher AES-256-CBC                   secret /etc/openvpn/static.key
auth SHA256                          cipher AES-256-CBC
port 1194                            auth SHA256
proto udp                            persist-tun
keepalive 10 60                      persist-key
persist-tun                          verb 3
persist-key
verb 3
```

Entrambi gli estremi nominano il cifrario: OpenVPN 2.6 su OpenSSL 3 non ha più il BF-CBC
predefinito della chiave statica (`Cipher BF-CBC not supported` e uscita fatale: la prima cosa che il test di questo lab ha trovato).

Prima il server, in primo piano per leggerne il log, poi il client:

```bash
sudo openvpn --config /etc/openvpn/server.conf          # server
sudo openvpn --config /etc/openvpn/client.ovpn --daemon # client; poi: ping 10.20.0.1
```

Entrambi stampano `Initialization Sequence Completed`. `--daemon` manda il client in
background; lo fermi con `sudo pkill openvpn`.

## Esercizio 3: cosa vede la rete

Con un tunnel su, cattura sul server mentre il client ci fa passare dei ping:

```bash
sudo tcpdump -ni vpn-lan udp port 51820     # WireGuard: UDP cifrato sul segmento
sudo tcpdump -ni wg0                        # gli stessi ping, in chiaro, dentro il tunnel
sudo tcpdump -ni vpn-lan udp port 1194      # OpenVPN sul segmento; tun0 per il lato in chiaro
```

Quel contrasto è tutto il senso di una VPN: sul segmento non passa niente di leggibile.

## Esercizio 4: entrano solo la VPN e SSH

Sul server, permetti quello che serve e scarta il resto. SSH per primo: l'SSH di vmctl (e il tuo)
arriva dalla scheda NAT, e un lab che si chiude fuori da solo non diverte.

```bash
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 1194 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo iptables -L INPUT -n -v
```

Dal client, `ping 172.20.1.1` ora viene scartato mentre `ping 10.10.0.1` dentro WireGuard
risponde ancora. `sudo iptables -F` rimette tutto a posto.

## Test

```bash
vmctl group test vpn-lab          # tutti e cinque gli script, stack acceso se serve
bash vms/labs/vpn-lab/tests/test_03_wireguard_tunnel.sh
```

`test_01` le macchine e il segmento, `test_02` le chiavi, `test_03` un tunnel WireGuard vero
(costruito e smontato), `test_04` un tunnel OpenVPN a chiave statica vero (demoni avviati e
fermati), `test_05` il recinto iptables (applicato e svuotato). Ogni script lascia il lab come l'ha trovato.

## Spegnere e pulire

```bash
vmctl group down vpn-lab
vmctl group clean vpn-lab         # cancella i due dischi overlay (chiede prima); l'immagine cloud resta in isos/
```
