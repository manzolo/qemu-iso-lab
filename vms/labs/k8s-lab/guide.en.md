# Kubernetes lab: a three-node MicroK8s cluster

A control plane and two workers on an isolated segment (172.20.6.0/24), MicroK8s 1.32 on Ubuntu 24.04
cloud images. Inspired by [manzolo/multipass-microk8s-cluster-demo](https://github.com/manzolo/multipass-microk8s-cluster-demo),
rebuilt on vmctl: no Multipass, no template to clone, and nothing changed on the host.

| VM | Role | Segment | From the host |
|---|---|---|---|
| `k8s-lab-main` | control plane, also runs pods | 172.20.6.1 | SSH 127.0.0.1:2369, NodePort 30080 at http://127.0.0.1:8089 |
| `k8s-lab-node1` | worker | 172.20.6.11 | SSH 127.0.0.1:2370 |
| `k8s-lab-node2` | worker | 172.20.6.12 | SSH 127.0.0.1:2371 |

About 10 GB of RAM while the three run (4 + 3 + 3).

## Start

```sh
vmctl group install k8s-lab    # three cloud images, the MicroK8s snap on each, then up
vmctl shell k8s-lab-main
```

The workers join by themselves. At every boot on the segment, `/usr/local/sbin/k8s-lab-node` (a systemd
unit) sets the node's place in the cluster:

- every VM also has QEMU's NAT NIC, and there every VM is 10.0.2.15. The kubelet therefore gets
  `--node-ip` with the segment address, the API server `--advertise-address`, and Calico the `k8s-lan`
  interface: three nodes with the same IP would be no cluster.
- the control plane publishes a fixed join token, and the workers run `microk8s join ... --worker` with
  it once. The token is in the repository, which is fine here: the segment lives on the host's loopback
  and nothing outside the host can reach it. A real cluster uses `microk8s add-node`'s one-time token.

The first `group up` after the install takes a few minutes: the workers wait for the control plane.
`journalctl -u k8s-lab-node` on any node shows what it did.

## Exercise 1: the cluster's anatomy

```sh
kubectl get nodes -o wide                  # three nodes Ready, INTERNAL-IP on 172.20.6.0/24
kubectl cluster-info
kubectl get pods -n kube-system -o wide    # calico-node on every node, CoreDNS
microk8s status | head -8
```

On a worker (`vmctl shell k8s-lab-node1`), `microk8s status` says it is acting as a node in a cluster:
the API server, the scheduler and the datastore live only on the control plane.

## Exercise 2: a Deployment and a NodePort Service

A Deployment keeps a number of identical pods running; the scheduler spreads them over the nodes. A
Service gives them one address, and a NodePort Service opens the same port on every node, whichever
node the pods are on.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3
kubectl rollout status deployment/web
kubectl create service nodeport web --tcp=80:80 --node-port=30080
kubectl get pods -o wide                   # NODE: the replicas on different nodes
kubectl get service web                    # 80:30080/TCP
for ip in $(kubectl get pods -l app=web -o jsonpath='{.items[*].status.podIP}'); do curl -s -o /dev/null -w "$ip %{http_code}\n" http://$ip; done    # every pod from here, whatever its node
for ip in 172.20.6.1 172.20.6.11 172.20.6.12; do curl -s http://$ip:30080 | grep -o '<title>.*</title>'; done
```

From the host, the control plane's 30080 is forwarded to 8089:

```sh
curl -s http://127.0.0.1:8089 | grep -o '<title>.*</title>'
```

Try it:

```sh
kubectl delete pod $(kubectl get pods -l app=web -o name | head -n1 | cut -d/ -f2) && kubectl get pods -l app=web    # a new pod replaces it
kubectl scale deployment web --replicas=6 && kubectl get pods -o wide
kubectl delete service web && kubectl delete deployment web    # back to an empty default namespace
```

## Exercise 3: scale, rolling update, rollback

Scaling changes only the number of pods. Changing the image starts a rolling update: new pods come up
while the old ones go away, a few at a time, so the Service never stops answering. Every change of the
pod template is a revision, and `rollout undo` goes back to the previous one.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3 && kubectl rollout status deployment/web
kubectl scale deployment web --replicas=5 && kubectl get pods -l app=web -o wide
kubectl set image deployment/web nginx=nginx:1.28-alpine && kubectl rollout status deployment/web    # old pods out, new pods in
kubectl rollout history deployment/web     # two revisions
kubectl get deployment web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'    # nginx:1.28-alpine
kubectl rollout undo deployment/web && kubectl rollout status deployment/web
kubectl get deployment web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'    # nginx:1.27-alpine again
kubectl delete deployment web
```

Try it: an image that does not exist. The new pods stay in `ErrImagePull` and the old ones keep serving:
a rolling update never takes away more than it has replaced.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=3 && kubectl set image deployment/web nginx=nginx:no-such-tag && sleep 20 && kubectl get pods -l app=web
kubectl rollout undo deployment/web && kubectl rollout status deployment/web && kubectl delete deployment web
```

## Exercise 4: drain a node

Before maintenance a node is drained: it is cordoned (no new pods) and its pods are evicted, so their
Deployments recreate them on the other nodes. DaemonSet pods such as `calico-node` stay, one per node by
definition. `uncordon` gives the node back to the scheduler; the pods already moved stay where they are.

```sh
kubectl create deployment web --image=nginx:1.27-alpine --replicas=6 && kubectl rollout status deployment/web
kubectl get pods -l app=web -o wide        # some on k8s-lab-node2
kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data
kubectl get nodes                          # k8s-lab-node2 Ready,SchedulingDisabled
kubectl get pods -l app=web -o wide        # all six on main and node1
kubectl uncordon k8s-lab-node2 && kubectl get nodes
kubectl delete deployment web
```

## Exercise 5: ConfigMap and Secret

Configuration lives outside the image: a ConfigMap holds plain settings, a Secret holds credentials.
Both reach a pod as environment variables or as files in a volume.

```sh
kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=blue
kubectl create secret generic web-secret --from-literal=PASSWORD=labsecret
cat ~/k8s/env-demo.yaml                    # envFrom: every key as a variable; a volume: every key as a file
kubectl apply -f ~/k8s/env-demo.yaml
kubectl wait --for=condition=Ready pod/env-demo --timeout=120s
kubectl logs env-demo                      # GREETING=hello COLOR=blue PASSWORD=labsecret
kubectl exec env-demo -- ls /config        # one file per key
kubectl exec env-demo -- cat /config/COLOR; echo    # a ConfigMap value has no newline
kubectl get secret web-secret -o jsonpath='{.data.PASSWORD}' | base64 -d; echo    # base64, not encryption
```

A Secret is only base64-encoded: who may read Secrets in the namespace reads the password.

Try it: change the ConfigMap. The file in the volume follows within a minute; the environment variable
keeps the value the pod started with.

```sh
kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=red -o yaml --dry-run=client | kubectl apply -f -
sleep 70; kubectl exec env-demo -- cat /config/COLOR; echo    # red
kubectl delete pod env-demo && kubectl delete configmap web-config && kubectl delete secret web-secret
```

## Exercise 6: a persistent volume, MariaDB that survives its pod

A pod's own files die with it. A PersistentVolumeClaim asks for storage that outlives the pod; MicroK8s'
`hostpath-storage` addon answers it with a directory on the node that runs the pod. The database
password travels as a Secret.

```sh
microk8s enable hostpath-storage           # the default StorageClass: stays enabled
kubectl create secret generic mariadb-root --from-literal=password=labroot
cat ~/k8s/mariadb.yaml                     # a PersistentVolumeClaim, and a Deployment that mounts it on /var/lib/mysql
kubectl apply -f ~/k8s/mariadb.yaml
kubectl rollout status deployment/mariadb --timeout=300s && kubectl get pvc,pv
sleep 15; kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e "CREATE DATABASE shop; CREATE TABLE shop.items (name VARCHAR(20)); INSERT INTO shop.items VALUES ('kept');"
kubectl delete pod -l app=mariadb && kubectl rollout status deployment/mariadb
sleep 15; kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e 'SELECT * FROM shop.items;'    # kept: a new pod, the same data
kubectl get pv -o wide                     # the volume and where it lives
kubectl delete deployment mariadb && kubectl delete pvc mariadb-data && kubectl delete secret mariadb-root
```

Deleting the claim deletes the volume too (reclaim policy `Delete`).

## The Kubernetes course: an application in eight episodes

The eight episodes of the course (tour page, *Courses*) build one small application in the namespace
`shop`, from manifests that the install copies to `~/k8s/shop` on the control plane: Redis for a
shopping list, [RustFS](https://github.com/rustfs/rustfs) for notes and backups (an S3 object store
like MinIO, which stopped publishing public images in 2025), and `app.py`, a Python app run from a
ConfigMap on the stock image that says which pod, IP and node answered. Its page polls `/whoami` every
second: the replicas take turns, a scale adds pods, a drained node disappears. The app is
http://127.0.0.1:8089 on the host, the RustFS console http://127.0.0.1:8090/rustfs/console/
(NodePort 30081; account `labadmin`, key `labsecret123`).

```sh
kubectl create namespace shop && kubectl config set-context --current --namespace=shop
cd ~/k8s/shop
kubectl create deployment hello --image=manzolo/demo-go:0.2.0 --replicas=3        # 2: a pod that says who it is
kubectl create service nodeport hello --tcp=80:8080 --node-port=30080 && curl -s localhost:30080
kubectl delete deployment hello && kubectl delete service hello

kubectl apply -f redis.yaml                                                      # 3: claim, Deployment, Service
kubectl run redis-test --image=redis:7-alpine --rm -i --restart=Never -- redis-cli -h redis ping
kubectl create secret generic s3-credentials --from-literal=access-key=labadmin --from-literal=secret-key=labsecret123
kubectl apply -f rustfs.yaml                                                     # 4: the object store
kubectl create configmap shop-code --from-file=app.py --from-file=backup.py
kubectl apply -f shop.yaml                                                       # 5: the app, three replicas
kubectl logs -l app=shop --prefix --tail=4

kubectl scale deployment shop --replicas=6                                       # 6: watch the page's panel
kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data && kubectl uncordon k8s-lab-node2
kubectl set env deployment/shop TITLE='Weekend shopping' COLOR=purple && kubectl rollout undo deployment/shop

kubectl scale deployment redis --replicas=0 && kubectl get endpoints shop        # 7: readiness empties the Service
kubectl scale deployment redis --replicas=1
microk8s enable metrics-server && kubectl top pods
kubectl autoscale deployment shop --cpu-percent=50 --min=3 --max=9 && kubectl apply -f load.yaml
kubectl get hpa                                                                  # up to nine replicas
kubectl delete -f load.yaml

kubectl apply -f backup.yaml                                                     # 8: Redis into RustFS, every 2 min
kubectl create job --from=cronjob/redis-backup backup-now && kubectl logs job/backup-now
kubectl delete namespace shop                                                    # everything, volumes included
```

Redis and RustFS are pinned to the control plane (`nodeSelector`): a `hostpath-storage` volume is a
directory on one node's disk, so their pods could not follow a drain to another node anyway.

## Tests

```sh
vmctl group test k8s-lab    # one test per exercise, each leaves the cluster as it found it
```
