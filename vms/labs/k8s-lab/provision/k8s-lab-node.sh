#!/bin/bash
# k8s-lab: at every boot this node takes its place in the cluster on the lab segment (k8s-lan).
#
# Every VM also has QEMU's NAT NIC, and on every VM it is 10.0.2.15: left to their defaults the
# kubelet would report that address and Calico would route pods over it, three nodes with the same
# IP. So the kubelet gets --node-ip, the API server --advertise-address and Calico the segment
# interface. k8s-lab-main publishes a fixed join token (the segment lives on the host's loopback,
# nothing outside the host reaches it); the workers join with it once and remember it.
#
# The install boot has no segment NIC: nothing to do, microk8s stays the single node it was born.
set -u
SEG=k8s-lan
MAIN=172.20.6.1
TOKEN=6b38736c61622d6a6f696e2d746f6b65
ARGS=/var/snap/microk8s/current/args
LOCK=/var/snap/microk8s/current/var/lock
mk() { /snap/bin/microk8s "$@"; }

ME=""
for _ in $(seq 1 30); do
    ME=$(ip -4 -o addr show dev "$SEG" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)
    [ -n "$ME" ] && break
    sleep 2
done
if [ -z "$ME" ]; then
    echo "k8s-lab: no $SEG address, not on the lab segment (install boot): nothing to do"
    exit 0
fi

# set_arg <file> <flag> <value>: one line --flag=value in a microk8s args file.
changed=0
set_arg() {
    grep -qx -- "$2=$3" "$ARGS/$1" && return 0
    sed -i "/^$2=/d" "$ARGS/$1"
    echo "$2=$3" >> "$ARGS/$1"
    changed=1
}
set_arg kubelet --node-ip "$ME"
[ "$ME" = "$MAIN" ] && set_arg kube-apiserver --advertise-address "$ME"

# The kubelet serves on the certificate microk8s made at the install, when there was no segment NIC:
# its names are 10.0.2.15 only, so `kubectl exec` and `kubectl logs` of a pod on this node failed
# with "x509: certificate is valid for 10.0.2.15, not 172.20.6.1" (2026-10-08). A worker gets a new
# one when it joins; the control plane never does, and the apiservice-kicker that would refresh the
# server's certificates stands aside once --advertise-address is set. Sign it again, same key and
# subject, with the segment address in it.
CERTS=/var/snap/microk8s/current/certs
if ! openssl x509 -in "$CERTS/kubelet.crt" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$ME\b"; then
    host=$(hostname | tr '[:upper:]' '[:lower:]')
    sans=$(openssl x509 -in "$CERTS/kubelet.crt" -noout -ext subjectAltName | tail -n1 | sed 's/IP Address:/IP:/g; s/ //g')
    openssl req -new -sha256 -key "$CERTS/kubelet.key" -subj "/CN=system:node:$host/O=system:nodes" \
        -addext "subjectAltName=$sans,IP:$ME" -out /tmp/k8s-lab-kubelet.csr
    openssl x509 -req -sha256 -in /tmp/k8s-lab-kubelet.csr -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" \
        -CAcreateserial -days 3650 -copy_extensions copy -out "$CERTS/kubelet.crt"
    rm -f /tmp/k8s-lab-kubelet.csr
    echo "k8s-lab: kubelet certificate signed again with $ME"
    changed=1
fi
if [ "$changed" = 1 ]; then
    echo "k8s-lab: $ME on $SEG, restarting microk8s"
    snap restart microk8s
fi

if [ "$ME" = "$MAIN" ]; then
    mk status --wait-ready --timeout 600 >/dev/null
    # Calico's default, first-found, would take the NAT NIC. Once a worker joins, microk8s rewrites its
    # own manifest with can-reach=<a node on the segment>, which is right too: set the interface only
    # while the default is still there, or the two settings take turns and roll calico-node at every boot.
    method=$(mk kubectl -n kube-system get daemonset calico-node \
        -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="IP_AUTODETECTION_METHOD")].value}')
    case "$method" in
        ""|first-found) mk kubectl -n kube-system set env daemonset/calico-node IP_AUTODETECTION_METHOD="interface=$SEG" ;;
    esac
    grep -q "^$TOKEN" /var/snap/microk8s/current/credentials/cluster-tokens.txt 2>/dev/null \
        || mk add-node --token "$TOKEN" --token-ttl 315360000 >/dev/null
    echo "k8s-lab: control plane on $ME, join token published"
    exit 0
fi

joined() { [ -e "$LOCK/clustered.lock" ] || mk status 2>&1 | grep -q 'acting as a node'; }
if joined; then
    echo "k8s-lab: worker $ME, already in the cluster"
    exit 0
fi
mk status --wait-ready --timeout 600 >/dev/null
# The control plane may still be starting: retry for half an hour.
for _ in $(seq 1 120); do
    if mk join "$MAIN:25000/$TOKEN" --worker; then
        echo "k8s-lab: worker $ME joined $MAIN"
        exit 0
    fi
    sleep 15
done
echo "k8s-lab: could not join $MAIN" >&2
exit 1
