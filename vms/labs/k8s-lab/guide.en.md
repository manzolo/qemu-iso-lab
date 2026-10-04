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

## Tests

```sh
vmctl group test k8s-lab    # one test per exercise, each leaves the cluster as it found it
```
