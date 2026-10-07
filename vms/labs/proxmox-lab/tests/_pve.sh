#!/usr/bin/env bash
# Shared by the proxmox-lab tests. `vmctl group test` may have just started the stack: wait for a
# quorate three-node cluster and for the IT-Tools container (CT 200) running on node 1, up to 5 min.
N1=proxmox-ve
N2=proxmox-ve-node2
N3=proxmox-ve-node3
C=proxmox-lab-client
CT=200
pve() { local vm="$1"; shift; on "$vm" "$@" 2>&1 || true; }
# ha_where: the node and state HA reports for ct:200 ("proxmox-ve-node2 started"), empty if not
# managed. Asked to node 2: the failover test powers node 1 off.
ha_where() { on "$N2" "ha-manager status | sed -n 's/^service ct:$CT (\\(.*\\), \\(.*\\))\$/\\1 \\2/p'" 2>/dev/null || true; }
# wait_ha <node> <state> [seconds]: until HA reports ct:200 there (true) or the time runs out (false).
wait_ha() {
    local want="$1 $2" limit="${3:-180}" i
    for i in $(seq 1 $((limit / 3))); do
        [ "$(ha_where)" = "$want" ] && return 0
        sleep 3
    done
    return 1
}
# The page the client gets from IT-Tools on the lab segment ("IT Tools" in its title).
it_tools() { on "$C" "curl -s -m 5 http://10.10.10.20/ | grep -o '<title>IT Tools'" 2>/dev/null || true; }
# replicate: the two jobs of the lesson, run now, until both are OK.
replicate() {
    pve "$N1" "pvesr list | grep -q '^$CT-0 ' || pvesr create-local-job $CT-0 $N2 --schedule '*/1'
               pvesr list | grep -q '^$CT-1 ' || pvesr create-local-job $CT-1 $N3 --schedule '*/1'" >/dev/null
    pve "$N1" "pvesr schedule-now $CT-0; pvesr schedule-now $CT-1" >/dev/null
    local i
    for i in $(seq 1 40); do
        [ "$(on "$N1" "pvesr status | awk 'NR>1 && \$NF==\"OK\" && \$4!=\"-\"' | wc -l" 2>/dev/null || true)" = "2" ] && return 0
        sleep 5
    done
    return 1
}
# pve_teardown: the cluster as installed: CT 200 home on node 1, out of HA, no replication job.
pve_teardown() {
    if [ -n "$(ha_where)" ]; then
        case "$(ha_where)" in
            "$N1 "*) ;;
            *)  # Replicate toward node 1 first: a migration that finds a stale volume there copies
                # the disk whole under a new name and leaves the old one behind (seen after test 5).
                sync_jobs "$(ha_where | cut -d' ' -f1)"
                pve "$N1" "ha-manager migrate ct:$CT $N1" >/dev/null; wait_ha "$N1" started 240 || true ;;
        esac
        pve "$N1" "ha-manager remove ct:$CT" >/dev/null
    fi
    pve "$N1" "for j in \$(pvesr list | awk 'NR>1 {print \$1}'); do pvesr delete \$j; done" >/dev/null
    local i n
    for i in $(seq 1 40); do
        [ -z "$(on "$N1" "pvesr list | awk 'NR>1'" 2>/dev/null || true)" ] && break
        sleep 3
    done
    # Volumes of CT 200 its configuration no longer names (a failover leaves them): destroyed.
    for n in "$N1" "$N2" "$N3"; do
        pve "$n" "keep=\$(pct config $CT 2>/dev/null | sed -n 's/^rootfs: local-zfs:\([^,]*\).*/\1/p')
                  [ -n \"\$keep\" ] && [ -f /etc/pve/nodes/$N1/lxc/$CT.conf ] || exit 0
                  for v in \$(zfs list -H -o name | grep '^rpool/data/subvol-$CT-disk-'); do
                      [ \"\${v##*/}\" = \"\$keep\" ] && [ $n = $N1 ] && continue
                      zfs destroy -r \"\$v\"
                  done" >/dev/null
    done
}
# sync_jobs <node>: run the replication jobs now from the node that holds the CT, until OK.
sync_jobs() {
    local from="$1" i
    pve "$from" "for j in \$(pvesr list | awk 'NR>1 {print \$1}'); do pvesr schedule-now \$j; done" >/dev/null
    for i in $(seq 1 40); do
        [ -z "$(on "$from" "pvesr status | awk 'NR>1 && \$NF!=\"OK\"'" 2>/dev/null || true)" ] && return 0
        sleep 5
    done
}
for _ in $(seq 1 60); do
    s=$(on "$N1" "pvecm status 2>/dev/null | grep -E '^(Nodes|Quorate):' | tr -s ' ' | tr '\n' ' '" 2>/dev/null || true)
    [ "$s" = "Nodes: 3 Quorate: Yes " ] && break
    sleep 5
done
for _ in $(seq 1 60); do
    [ "$(on "$N1" "pct status $CT" 2>/dev/null || true)" = "status: running" ] && break
    sleep 5
done
