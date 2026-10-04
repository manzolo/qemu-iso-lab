# Lab Kubernetes: un cluster MicroK8s a tre nodi

Un control plane e due worker su un segmento isolato (172.20.6.0/24), MicroK8s 1.32 su cloud image
Ubuntu 24.04. Ispirato a [manzolo/multipass-microk8s-cluster-demo](https://github.com/manzolo/multipass-microk8s-cluster-demo),
rifatto su vmctl: niente Multipass, nessun template da clonare e niente di modificato sull'host.

| VM | Ruolo | Segmento | Dall'host |
|---|---|---|---|
| `k8s-lab-main` | control plane, esegue anche pod | 172.20.6.1 | SSH 127.0.0.1:2369, NodePort 30080 su http://127.0.0.1:8089 |
| `k8s-lab-node1` | worker | 172.20.6.11 | SSH 127.0.0.1:2370 |
| `k8s-lab-node2` | worker | 172.20.6.12 | SSH 127.0.0.1:2371 |

Circa 10 GB di RAM mentre girano tutte e tre (4 + 3 + 3).

## Avvio

```sh
vmctl group install k8s-lab    # tre cloud image, lo snap di MicroK8s su ognuna, poi up
vmctl shell k8s-lab-main
```

I worker entrano nel cluster da soli. A ogni avvio sul segmento, `/usr/local/sbin/k8s-lab-node` (un'unità
systemd) mette il nodo al suo posto:

- ogni VM ha anche la scheda NAT di QEMU, e lì tutte le VM sono 10.0.2.15. Per questo il kubelet riceve
  `--node-ip` con l'indirizzo del segmento, l'API server `--advertise-address` e Calico l'interfaccia
  `k8s-lan`: tre nodi con lo stesso IP non farebbero un cluster.
- il control plane pubblica un token di join fisso, e i worker lo usano una volta con
  `microk8s join ... --worker`. Il token è nel repository, e qui va bene: il segmento vive sul loopback
  dell'host e niente fuori dall'host lo raggiunge. Un cluster vero usa il token monouso di
  `microk8s add-node`.

Il primo `group up` dopo l'installazione richiede qualche minuto: i worker aspettano il control plane.
`journalctl -u k8s-lab-node` su qualunque nodo mostra cosa ha fatto.

## Esercizio 1: anatomia del cluster

```sh
kubectl get nodes -o wide                  # tre nodi Ready, INTERNAL-IP su 172.20.6.0/24
kubectl cluster-info
kubectl get pods -n kube-system -o wide    # calico-node su ogni nodo, CoreDNS
microk8s status | head -8
```

Su un worker (`vmctl shell k8s-lab-node1`), `microk8s status` dice che fa da nodo in un cluster: API
server, scheduler e datastore stanno solo sul control plane.

## Esercizio 2: un Deployment e un Service NodePort

Un Deployment tiene in vita un certo numero di pod identici; lo scheduler li distribuisce sui nodi. Un
Service dà loro un indirizzo unico, e un Service NodePort apre la stessa porta su ogni nodo, qualunque
sia il nodo su cui girano i pod.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3
kubectl rollout status deployment/web
kubectl create service nodeport web --tcp=80:80 --node-port=30080
kubectl get pods -o wide                   # NODE: le repliche su nodi diversi
kubectl get service web                    # 80:30080/TCP
for ip in $(kubectl get pods -l app=web -o jsonpath='{.items[*].status.podIP}'); do curl -s -o /dev/null -w "$ip %{http_code}\n" http://$ip; done    # ogni pod da qui, su qualunque nodo
for ip in 172.20.6.1 172.20.6.11 172.20.6.12; do curl -s http://$ip:30080 | grep -o '<title>.*</title>'; done
```

Dall'host, la 30080 del control plane è inoltrata sulla 8089:

```sh
curl -s http://127.0.0.1:8089 | grep -o '<title>.*</title>'
```

Prova:

```sh
kubectl delete pod $(kubectl get pods -l app=web -o name | head -n1 | cut -d/ -f2) && kubectl get pods -l app=web    # un pod nuovo lo sostituisce
kubectl scale deployment web --replicas=6 && kubectl get pods -o wide
kubectl delete service web && kubectl delete deployment web    # il namespace default torna vuoto
```

## Test

```sh
vmctl group test k8s-lab    # un test per esercizio, ognuno lascia il cluster come l'ha trovato
```
