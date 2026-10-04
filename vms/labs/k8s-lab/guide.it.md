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

## Esercizio 3: scale, rolling update, rollback

Scalare cambia solo il numero di pod. Cambiare l'immagine avvia un rolling update: i pod nuovi salgono
mentre i vecchi se ne vanno, pochi alla volta, così il Service non smette mai di rispondere. Ogni modifica
del template dei pod è una revisione, e `rollout undo` torna alla precedente.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3 && kubectl rollout status deployment/web
kubectl scale deployment web --replicas=5 && kubectl get pods -l app=web -o wide
kubectl set image deployment/web nginx=nginx:1.28-alpine && kubectl rollout status deployment/web    # escono i vecchi, entrano i nuovi
kubectl rollout history deployment/web     # due revisioni
kubectl get deployment web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'    # nginx:1.28-alpine
kubectl rollout undo deployment/web && kubectl rollout status deployment/web
kubectl get deployment web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'    # di nuovo nginx:1.27-alpine
kubectl delete deployment web
```

Prova: un'immagine che non esiste. I pod nuovi restano in `ErrImagePull` e i vecchi continuano a servire:
un rolling update non toglie mai più di quanto ha già sostituito.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3 && kubectl set image deployment/web nginx=nginx:no-such-tag && sleep 20 && kubectl get pods -l app=web
kubectl rollout undo deployment/web && kubectl rollout status deployment/web && kubectl delete deployment web
```

## Esercizio 4: drain di un nodo

Prima di una manutenzione si fa il drain del nodo: viene messo in cordon (niente pod nuovi) e i suoi pod
vengono sfrattati, così i loro Deployment li ricreano sugli altri nodi. I pod dei DaemonSet come
`calico-node` restano, uno per nodo per definizione. `uncordon` restituisce il nodo allo scheduler; i pod
già spostati restano dove sono.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=6 && kubectl rollout status deployment/web
kubectl get pods -l app=web -o wide        # alcuni su k8s-lab-node2
kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data
kubectl get nodes                          # k8s-lab-node2 Ready,SchedulingDisabled
kubectl get pods -l app=web -o wide        # tutti e sei su main e node1
kubectl uncordon k8s-lab-node2 && kubectl get nodes
kubectl delete deployment web
```

## Esercizio 5: ConfigMap e Secret

La configurazione sta fuori dall'immagine: una ConfigMap contiene impostazioni in chiaro, un Secret le
credenziali. Entrambi arrivano al pod come variabili d'ambiente o come file in un volume.

```sh
kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=blue
kubectl create secret generic web-secret --from-literal=PASSWORD=labsecret
cat ~/k8s/env-demo.yaml                    # envFrom: ogni chiave come variabile; un volume: ogni chiave come file
kubectl apply -f ~/k8s/env-demo.yaml
kubectl wait --for=condition=Ready pod/env-demo --timeout=120s
kubectl logs env-demo                      # GREETING=hello COLOR=blue PASSWORD=labsecret
kubectl exec env-demo -- ls /config        # un file per chiave
kubectl exec env-demo -- cat /config/COLOR; echo    # il valore di una ConfigMap non ha a capo
kubectl get secret web-secret -o jsonpath='{.data.PASSWORD}' | base64 -d; echo    # base64, non cifratura
```

Un Secret è solo codificato in base64: chi può leggere i Secret del namespace legge la password.

Prova: cambia la ConfigMap. Il file nel volume si aggiorna entro un minuto; la variabile d'ambiente resta
quella con cui il pod è partito.

```sh
kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=red -o yaml --dry-run=client | kubectl apply -f -
sleep 70; kubectl exec env-demo -- cat /config/COLOR; echo    # red
kubectl delete pod env-demo && kubectl delete configmap web-config && kubectl delete secret web-secret
```

## Esercizio 6: un volume persistente, MariaDB che sopravvive al suo pod

I file di un pod muoiono con lui. Una PersistentVolumeClaim chiede uno spazio che sopravvive al pod;
l'addon `hostpath-storage` di MicroK8s la soddisfa con una directory sul nodo che esegue il pod. La
password del database viaggia come Secret.

```sh
microk8s enable hostpath-storage           # la StorageClass di default: resta abilitata
kubectl create secret generic mariadb-root --from-literal=password=labroot
cat ~/k8s/mariadb.yaml                     # una PersistentVolumeClaim, e un Deployment che la monta su /var/lib/mysql
kubectl apply -f ~/k8s/mariadb.yaml
kubectl rollout status deployment/mariadb --timeout=300s && kubectl get pvc,pv
sleep 15; kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e "CREATE DATABASE shop; CREATE TABLE shop.items (name VARCHAR(20)); INSERT INTO shop.items VALUES ('kept');"
kubectl delete pod -l app=mariadb && kubectl rollout status deployment/mariadb
sleep 15; kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e 'SELECT * FROM shop.items;'    # kept: un pod nuovo, gli stessi dati
kubectl get pv -o wide                     # il volume e dove sta
kubectl delete deployment mariadb && kubectl delete pvc mariadb-data && kubectl delete secret mariadb-root
```

Cancellare la claim cancella anche il volume (reclaim policy `Delete`).

## Test

```sh
vmctl group test k8s-lab    # un test per esercizio, ognuno lascia il cluster come l'ha trovato
```
