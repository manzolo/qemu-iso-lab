export const title = { en: "Kubernetes on three VMs: the k8s lab", it: "Kubernetes su tre VM: il lab k8s" };
export const series = "labs";
export const lab = "k8s-lab";

const LAB = "k8s-lab";
const VM = "k8s-lab-main";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
// kubectl's -o wide is wider than the terminal: the columns the lesson talks about, nothing else.
const NODES = "kubectl get nodes -o custom-columns=NODE:.metadata.name,IP:.status.addresses[0].address,VERSION:.status.nodeInfo.kubeletVersion,READY:.status.conditions[-1].status";
const PODS = (sel) => `kubectl get pods ${sel} -o custom-columns=POD:.metadata.name,STATUS:.status.phase,NODE:.spec.nodeName,IP:.status.podIP`;
const IMAGE = "kubectl get deployment web -o jsonpath='{.spec.template.spec.containers[0].image}{\"\\n\"}'";

export const cues = [
  { id: "intro", en: "Kubernetes runs containers on many machines as if they were one. To learn it you need a real cluster: the k8s lab is three virtual machines with MicroK8s, a control plane and two workers.",
    it: "Kubernetes fa girare i container su tante macchine come se fossero una sola. Per impararlo serve un cluster vero: il lab k8s sono tre macchine virtuali con MicroK8s, un control plane e due worker." },
  { id: "install", en: "One command installs the three of them: a cloud image each, with the MicroK8s snap.",
    it: "Un comando le installa tutte e tre: una cloud image ciascuna, con lo snap di MicroK8s." },
  { id: "join", en: "Then they start on their own network segment, and the workers join the control plane by themselves. Nothing to type.",
    it: "Poi partono sul loro segmento di rete, e i worker si uniscono al control plane da soli. Niente da digitare." },
  { id: "anatomy", en: "Three nodes, ready, each with its address on the lab segment. In kube-system run the cluster's own pods: Calico, the pod network, on every node, and CoreDNS.",
    it: "Tre nodi, pronti, ognuno con il suo indirizzo sul segmento del lab. In kube-system girano i pod del cluster stesso: Calico, la rete dei pod, su ogni nodo, e CoreDNS." },
  { id: "deploy", en: "A Deployment keeps three nginx pods running, and the scheduler spreads them over the nodes. A NodePort Service opens port 30080 on every node: each one answers.",
    it: "Un Deployment tiene in vita tre pod nginx, e lo scheduler li distribuisce sui nodi. Un Service NodePort apre la porta 30080 su ogni nodo: rispondono tutti." },
  { id: "browser", en: "The control plane's port is forwarded to the host: the same page, in the browser.",
    it: "La porta del control plane è inoltrata all'host: la stessa pagina, nel browser." },
  { id: "rollout", en: "A new image starts a rolling update: new pods in, old pods out, a few at a time. Every change is a revision, and rollout undo goes back.",
    it: "Un'immagine nuova avvia un rolling update: entrano i pod nuovi, escono i vecchi, pochi alla volta. Ogni modifica è una revisione, e rollout undo torna indietro." },
  { id: "drain", en: "Maintenance on a node: drain it. Its pods are evicted and recreated on the other two. Uncordon gives it back to the scheduler.",
    it: "Manutenzione su un nodo: lo si svuota con drain. I suoi pod vengono sfrattati e ricreati sugli altri due. Uncordon lo restituisce allo scheduler." },
  { id: "config", en: "Configuration lives outside the image. A ConfigMap and a Secret reach the pod as variables and as files. And a Secret is only base64: not encryption.",
    it: "La configurazione sta fuori dall'immagine. Una ConfigMap e un Secret arrivano al pod come variabili e come file. E un Secret è solo base64: non è cifratura." },
  { id: "volume", en: "A pod's files die with it. A persistent volume claim does not: MariaDB writes a row, its pod is deleted, and the new pod finds the row again.",
    it: "I file di un pod muoiono con lui. Una persistent volume claim no: MariaDB scrive una riga, il suo pod viene cancellato, e il pod nuovo ritrova la riga." },
  { id: "tests", en: "Six tests, one per exercise, and each leaves the cluster as it found it.",
    it: "Sei test, uno per esercizio, e ognuno lascia il cluster come l'ha trovato." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl group down ${LAB} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const guest = (d, cmd, read = 3500) => d.guest(cmd, { read });
// A command that waits on the cluster (image pulls, rollouts): fast-forwarded while it runs.
async function slow(d, cmd, read = 3500, factor = 6) {
  d.ff(factor);
  await d.guest(cmd, { read: 0, timeout: 600000 });
  d.ffEnd();
  await d.sleep(read);
}
const readyNodes = (d) => d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- "kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready '" 2>/dev/null || true`).trim();

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(4000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(20);
  for (let i = 0; i < 3600 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("join");
  await d.run("clear");
  await d.run(`vmctl group status ${LAB}`);
  d.ff(12);
  for (let i = 0; i < 900 && readyNodes(d) !== "3"; i++) await d.sleep(2000);
  // Ready comes before the pod network (the lab's tests wait for the same).
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- kubectl -n kube-system rollout status daemonset/calico-node --timeout=300s >/dev/null 2>&1 || true`);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.run("clear");
  await d.session(VM);
  await guest(d, NODES, 5000);
  await guest(d, PODS("-n kube-system"), 6000);

  await d.cue("deploy");
  await guest(d, "clear; kubectl create deployment web --image=nginx:1.27-alpine --replicas=3", 1500);
  await slow(d, "kubectl rollout status deployment/web", 1500);
  await guest(d, PODS("-l app=web"), 4500);
  await guest(d, "kubectl create service nodeport web --tcp=80:80 --node-port=30080", 1500);
  await guest(d, "for ip in 172.20.6.1 172.20.6.11 172.20.6.12; do echo -n \"$ip  \"; curl -s http://$ip:30080 | grep -o '<title>.*</title>'; done", 5000);
  await d.leave();

  await d.cue("browser");
  await d.focusBrowser();
  const page = d.page;
  await page.bringToFront();
  await page.goto("http://127.0.0.1:8089/");
  await d.sleep(5500);
  await page.goto("about:blank");
  await d.focusTerminal();

  await d.cue("rollout");
  await d.run("clear");
  await d.session(VM);
  await guest(d, "kubectl set image deployment/web nginx=nginx:1.28-alpine", 1000);
  await slow(d, "kubectl rollout status deployment/web", 2000, 4);
  await guest(d, "kubectl rollout history deployment/web", 3000);
  await guest(d, IMAGE, 2500);
  await slow(d, "kubectl rollout undo deployment/web && kubectl rollout status deployment/web", 2000, 4);
  await guest(d, IMAGE + "    # back", 3000);

  await d.cue("drain");
  await guest(d, "clear; kubectl scale deployment web --replicas=6", 1000);
  await slow(d, "kubectl rollout status deployment/web", 1000);
  await guest(d, PODS("-l app=web"), 4500);
  await slow(d, "kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data | tail -3", 2000, 4);
  await slow(d, "kubectl rollout status deployment/web", 500);
  await guest(d, "clear; " + PODS("-l app=web") + "    # none on node2", 4500);
  await guest(d, "kubectl get nodes", 3500);
  await guest(d, "kubectl uncordon k8s-lab-node2 && kubectl delete service web && kubectl delete deployment web", 2000);

  await d.cue("config");
  await guest(d, "clear; kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=blue", 1000);
  await guest(d, "kubectl create secret generic web-secret --from-literal=PASSWORD=labsecret", 1000);
  await guest(d, "cat ~/k8s/env-demo.yaml", 6000);
  await guest(d, "kubectl apply -f ~/k8s/env-demo.yaml", 500);
  await slow(d, "kubectl wait --for=condition=Ready pod/env-demo --timeout=180s", 1000);
  await guest(d, "clear; kubectl logs env-demo", 3000);
  await guest(d, "kubectl exec env-demo -- ls /config; kubectl exec env-demo -- cat /config/COLOR", 3000);
  await guest(d, "kubectl get secret web-secret -o jsonpath='{.data.PASSWORD}'; echo", 2500);
  await guest(d, "kubectl get secret web-secret -o jsonpath='{.data.PASSWORD}' | base64 -d; echo", 3500);
  await guest(d, "kubectl delete pod env-demo --grace-period=1 >/dev/null; kubectl delete configmap web-config; kubectl delete secret web-secret", 1000);

  await d.cue("volume");
  await slow(d, "clear; microk8s enable hostpath-storage | tail -2", 1500, 6);
  await guest(d, "kubectl create secret generic mariadb-root --from-literal=password=labroot", 1000);
  await guest(d, "clear; grep -E 'kind|storage:|claimName|mountPath|secretKeyRef' ~/k8s/mariadb.yaml", 5000);
  await guest(d, "kubectl apply -f ~/k8s/mariadb.yaml", 500);
  await slow(d, "kubectl rollout status deployment/mariadb --timeout=600s && sleep 20", 1000, 8);
  await guest(d, "clear; kubectl get pvc", 3500);
  await guest(d, "kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e \"CREATE DATABASE shop; CREATE TABLE shop.items (name VARCHAR(20)); INSERT INTO shop.items VALUES ('kept');\"", 1500);
  await slow(d, "kubectl delete pod -l app=mariadb && kubectl rollout status deployment/mariadb && sleep 20", 1000, 6);
  await guest(d, PODS("-l app=mariadb") + "    # a new pod", 3500);
  await guest(d, "kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e 'SELECT * FROM shop.items;'", 4500);
  await guest(d, "kubectl delete deployment mariadb && kubectl delete pvc mariadb-data && kubectl delete secret mariadb-root", 1500);
  await d.leave();

  await d.cue("tests");
  await d.run("clear");
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(10);
  for (let i = 0; i < 1800 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
