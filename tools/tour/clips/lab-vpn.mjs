export const title = { en: "A VPN by hand: WireGuard on the VPN lab", it: "Una VPN a mano: WireGuard sul lab VPN" };
export const series = "labs";
export const lab = "vpn-lab";

const LAB = "vpn-lab";
const SERVER = "vpn-lab-server", CLIENT = "vpn-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "A VPN is a private network laid over another one: packets travel encrypted between two ends and come out in clear inside. The VPN lab is two machines on a private segment, WireGuard installed, nothing configured. We build the tunnel by hand.",
    it: "Una VPN è una rete privata stesa sopra un'altra: i pacchetti viaggiano cifrati fra due estremi e dentro escono in chiaro. Il lab VPN sono due macchine su un segmento privato, WireGuard installato, niente configurato. Il tunnel lo costruiamo a mano." },
  { id: "install", en: "Two cloud images: the server at 172.20.1.1, the client at 172.20.1.2.",
    it: "Due cloud image: il server a 172.20.1.1, il client a 172.20.1.2." },
  { id: "keys", en: "WireGuard is built on key pairs. On the server, wg genkey makes the private key and wg pubkey derives the public one. Only public keys ever travel.",
    it: "WireGuard si regge su coppie di chiavi. Sul server, wg genkey crea la chiave privata e wg pubkey ne ricava la pubblica. Viaggiano solo le chiavi pubbliche." },
  { id: "clientkeys", en: "The same on the client. Now each side has a secret and the other side's public key is all it needs to know.",
    it: "Lo stesso sul client. Ora ogni lato ha un segreto, e della controparte gli basta conoscere la chiave pubblica." },
  { id: "clientconf", en: "The client's wg0.conf: its tunnel address, its private key, and a Peer: the server's public key, where to reach it on the segment, and which addresses live behind it.",
    it: "Il wg0.conf del client: il suo indirizzo nel tunnel, la sua chiave privata, e un Peer: la chiave pubblica del server, dove raggiungerlo sul segmento, e quali indirizzi stanno dietro di lui." },
  { id: "clientup", en: "wg-quick up creates the interface. Nothing answers yet: the server knows nothing about this client.",
    it: "wg-quick up crea l'interfaccia. Ancora non risponde nessuno: il server non sa nulla di questo client." },
  { id: "serverconf", en: "On the server, the mirror image: listen on 51820, and a Peer with the client's public key. wg show: the handshake happened seconds ago.",
    it: "Sul server, l'immagine speculare: in ascolto sulla 51820, e un Peer con la chiave pubblica del client. wg show: l'handshake è di pochi secondi fa." },
  { id: "ping", en: "From the client, 10.10.0.1 is the server through the tunnel. Take the tunnel down and the address is gone; bring it up and it is back in a second.",
    it: "Dal client, 10.10.0.1 è il server attraverso il tunnel. Tira giù il tunnel e l'indirizzo sparisce; tiralo su ed è di nuovo lì in un secondo." },
  { id: "wire", en: "What the wire sees. tcpdump on the segment, while the client pings through the tunnel: only UDP on port 51820, ciphertext.",
    it: "Cosa vede il cavo. tcpdump sul segmento, mentre il client fa ping attraverso il tunnel: solo UDP sulla porta 51820, cifrato." },
  { id: "inside", en: "The same capture on wg0: the pings, in clear. That is a VPN in two captures.",
    it: "La stessa cattura su wg0: i ping, in chiaro. Ecco una VPN in due catture." },
  { id: "fence", en: "Last, fence the server with iptables: established traffic, SSH, the WireGuard port, then drop everything else.",
    it: "Infine, recinta il server con iptables: traffico stabilito, SSH, la porta di WireGuard, poi scarta tutto il resto." },
  { id: "fenced", en: "From the client, the server's segment address no longer answers a ping, but the tunnel still does: 51820 is allowed. Then the rules come off.",
    it: "Dal client, l'indirizzo del server sul segmento non risponde più al ping, ma il tunnel sì: la 51820 è permessa. Poi le regole vengono tolte." },
  { id: "tests", en: "Tunnels down, keys removed, and the lab's five tests rebuild and verify all of it, OpenVPN included: the guide has that second tunnel too.",
    it: "Tunnel giù, chiavi rimosse, e i cinque test del lab ricostruiscono e verificano tutto, OpenVPN compreso: nella guida c'è anche quel secondo tunnel." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; ./bin/vmctl stop ${SERVER} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const pubkey = (d, vm) => d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${vm} -- cat publickey`).trim();

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(3000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("keys");
  await d.run("clear");
  await d.session(SERVER);
  await d.guest("wg genkey | tee privatekey | wg pubkey > publickey && cat publickey", { read: 4000 });
  const serverKey = pubkey(d, SERVER);
  await d.leave();

  await d.cue("clientkeys");
  await d.session(CLIENT);
  await d.guest("wg genkey | tee privatekey | wg pubkey > publickey && cat publickey", { read: 3500 });
  const clientKey = pubkey(d, CLIENT);
  await d.cue("clientconf");
  await d.guest(`sudo tee /etc/wireguard/wg0.conf >/dev/null <<EOF
[Interface]
Address = 10.10.0.2/24
PrivateKey = $(cat privatekey)

[Peer]
PublicKey = ${serverKey}
Endpoint = 172.20.1.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 25
EOF`, { read: 5000, delay: 20 });
  await d.cue("clientup");
  await d.guest("sudo wg-quick up wg0", { read: 2500 });
  await d.guest("ping -c 2 -W 2 10.10.0.1    # nobody home yet", { read: 3000 });
  await d.leave();

  await d.cue("serverconf");
  await d.session(SERVER);
  await d.guest(`sudo tee /etc/wireguard/wg0.conf >/dev/null <<EOF
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = $(cat privatekey)

[Peer]
PublicKey = ${clientKey}
AllowedIPs = 10.10.0.2/32
EOF`, { read: 4000, delay: 20 });
  await d.guest("sudo wg-quick up wg0", { read: 2500 });
  await d.guest("sleep 3; sudo wg show", { read: 5000 });
  await d.leave();

  await d.cue("ping");
  await d.session(CLIENT);
  await d.guest("ping -c 3 10.10.0.1", { read: 3500 });
  await d.guest("sudo wg-quick down wg0 && ping -c 1 -W 2 10.10.0.1; sudo wg-quick up wg0", { read: 4000 });
  await d.leave();

  await d.cue("wire");
  await d.session(SERVER);
  // The client pings through the tunnel while the server captures: started out of frame, on the host.
  d.vm(`cd ${CHECKOUT} && (setsid -f ./bin/vmctl shell ${CLIENT} -- 'sleep 2; ping -c 6 -i 0.7 10.10.0.1' >/dev/null 2>&1 </dev/null; true)`);
  await d.guest("sudo timeout 6 tcpdump -ni vpn-lan udp port 51820 -c 6    # while the client pings 10.10.0.1", { read: 4500, timeout: 30000 });
  await d.cue("inside");
  d.vm(`cd ${CHECKOUT} && (setsid -f ./bin/vmctl shell ${CLIENT} -- 'sleep 2; ping -c 6 -i 0.7 10.10.0.1' >/dev/null 2>&1 </dev/null; true)`);
  await d.guest("sudo timeout 6 tcpdump -ni wg0 -c 6    # the same pings, in clear", { read: 4500, timeout: 30000 });

  await d.cue("fence");
  await d.guest("sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT; sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT; sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT; sudo iptables -A INPUT -j DROP", { read: 2500 });
  await d.guest("sudo iptables -L INPUT -n -v", { read: 4500 });
  await d.leave();

  await d.cue("fenced");
  await d.session(CLIENT);
  await d.guest("ping -c 1 -W 2 172.20.1.1 || echo 'dropped by the server, as intended'", { read: 3500 });
  await d.guest("ping -c 2 10.10.0.1    # the tunnel still works", { read: 3500 });
  await d.guest("sudo wg-quick down wg0; sudo rm -f /etc/wireguard/wg0.conf privatekey publickey", { read: 2000 });
  await d.leave();
  await d.session(SERVER);
  await d.guest("sudo iptables -F; sudo wg-quick down wg0; sudo rm -f /etc/wireguard/wg0.conf privatekey publickey", { read: 2500 });
  await d.leave();

  await d.cue("tests");
  await d.run("clear");
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
