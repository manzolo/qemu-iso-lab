// Lesson of the k8s-lab, rewritten 2026-10-07 after Manzolo's review of the clips: one short command
// per step (no &&, ;, for loops or -o custom-columns), the screen cleared with Ctrl+L, cleanups and
// waits off camera, the SQL typed in the mariadb client line by line. A smaller font, so that
// `kubectl get pods -o wide` fits without wrapping.
export const title = { en: "Kubernetes on three VMs: the k8s lab", it: "Kubernetes su tre VM: il lab k8s" };
export const series = "labs";
export const lab = "k8s-lab";

const LAB = "k8s-lab";
const VM = "k8s-lab-main";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "Kubernetes runs containers on many machines as if they were one. To learn it you need a real cluster: the k8s lab is three virtual machines with MicroK8s, a control plane and two workers.",
    it: "Kubernetes fa girare i container su tante macchine come se fossero una sola. Per impararlo serve un cluster vero: il lab k8s sono tre macchine virtuali con MicroK8s, un control plane e due worker." },
  { id: "install", en: "One command installs the three of them: a cloud image each, with the MicroK8s snap.",
    it: "Un comando le installa tutte e tre: una cloud image ciascuna, con lo snap di MicroK8s." },
  { id: "join", en: "Then they start on their own network segment, and the workers join the control plane by themselves. Nothing to type.",
    it: "Poi partono sul loro segmento di rete, e i worker si uniscono al control plane da soli. Niente da digitare." },
  { id: "anatomy", en: "Three nodes, ready. In kube-system run the cluster's own pods: Calico, the pod network, one on every node, and CoreDNS. The NODE column says where each one runs.",
    it: "Tre nodi, pronti. In kube-system girano i pod del cluster stesso: Calico, la rete dei pod, uno su ogni nodo, e CoreDNS. La colonna NODE dice dove gira ciascuno." },
  { id: "deploy", en: "A Deployment keeps three nginx pods running, and the scheduler spreads them over the nodes.",
    it: "Un Deployment tiene in vita tre pod nginx, e lo scheduler li distribuisce sui nodi." },
  { id: "service", en: "A NodePort Service opens port 30080 on every node: ask node one or node two, nginx answers from both.",
    it: "Un Service NodePort apre la porta 30080 su ogni nodo: chiedi al nodo uno o al nodo due, nginx risponde da tutti e due." },
  { id: "browser", en: "The control plane's port is forwarded to the host: the same page, in the browser.",
    it: "La porta del control plane è inoltrata all'host: la stessa pagina, nel browser." },
  { id: "rollout", en: "A new image starts a rolling update: new pods in, old pods out, a few at a time. Every change is a revision, and rollout undo goes back.",
    it: "Un'immagine nuova avvia un rolling update: entrano i pod nuovi, escono i vecchi, pochi alla volta. Ogni modifica è una revisione, e rollout undo torna indietro." },
  { id: "drain", en: "Maintenance on a node: drain it. Its pods are evicted and recreated on the other two. Uncordon gives it back to the scheduler.",
    it: "Manutenzione su un nodo: lo si svuota con drain. I suoi pod vengono sfrattati e ricreati sugli altri due. Uncordon lo restituisce allo scheduler." },
  { id: "config", en: "Configuration lives outside the image. A ConfigMap and a Secret reach the pod as variables and as files.",
    it: "La configurazione sta fuori dall'immagine. Una ConfigMap e un Secret arrivano al pod come variabili e come file." },
  { id: "secret", en: "And a Secret is only base64: anyone who may read it decodes it in one command. It is not encryption.",
    it: "E un Secret è solo base64: chi può leggerlo lo decodifica con un comando. Non è cifratura." },
  { id: "volume", en: "A pod's files die with it. A persistent volume claim does not: MariaDB keeps its data on a volume of its own.",
    it: "I file di un pod muoiono con lui. Una persistent volume claim no: MariaDB tiene i suoi dati su un volume tutto suo." },
  { id: "sql", en: "Write a row from the MariaDB client. Then delete the pod: the Deployment starts a new one, on the same volume.",
    it: "Scriviamo una riga dal client di MariaDB. Poi cancelliamo il pod: il Deployment ne avvia uno nuovo, sullo stesso volume." },
  { id: "kept", en: "A new pod, and the row is still there.",
    it: "Un pod nuovo, e la riga è ancora lì." },
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
  await d.openTerminal(14);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const say = (d, cmd, read = 4500) => d.guest(cmd, { read });
// A command that waits on the cluster (image pulls, rollouts): fast-forwarded while it runs.
async function slow(d, cmd, read = 3500, factor = 6) {
  d.ff(factor);
  await d.guest(cmd, { read: 0, timeout: 600000 });
  d.ffEnd();
  await d.sleep(read);
}
// Off camera, in the control plane: cleanups and waits the viewer does not need to watch.
const off = (d, cmd) => d.offCamera(VM, cmd);
async function offWait(d, cmd, factor = 8) {
  d.ff(factor);
  off(d, cmd);
  d.ffEnd();
}
// A line typed into an interactive client (no shell prompt to wait for).
async function typeLine(d, line, ms = 2500) {
  d.step(line);
  await d.type(" " + line, 40);
  await d.sleep(300);
  await d.key("Return");
  await d.sleep(ms);
}
const readyNodes = (d) => d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- "kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready '" 2>/dev/null || true`).trim();

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
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
  await d.clearScreen();
  await d.run(`vmctl group status ${LAB}`);
  d.ff(12);
  for (let i = 0; i < 900 && readyNodes(d) !== "3"; i++) await d.sleep(2000);
  // Ready comes before the pod network (the lab's tests wait for the same).
  off(d, "kubectl -n kube-system rollout status daemonset/calico-node --timeout=300s");
  d.ffEnd();
  await d.sleep(3000);

  await d.cue("anatomy");
  await d.clearScreen();
  await d.session(VM);
  await say(d, "kubectl get nodes", 5000);
  await say(d, "kubectl get pods -n kube-system -o wide", 8000);

  await d.cue("deploy");
  await d.clearScreen();
  await say(d, "kubectl create deployment web --image=nginx:1.27-alpine --replicas=3", 1500);
  await slow(d, "kubectl rollout status deployment/web", 1500);
  await say(d, "kubectl get pods -o wide", 7000);

  await d.cue("service");
  await d.clearScreen();
  await say(d, "kubectl create service nodeport web --tcp=80:80 --node-port=30080", 2000);
  await say(d, "kubectl get service web", 4000);
  await say(d, "curl -sI http://172.20.6.11:30080", 3500);
  await say(d, "curl -sI http://172.20.6.12:30080", 4500);
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
  await d.clearScreen();
  await d.session(VM);
  await say(d, "kubectl set image deployment/web nginx=nginx:1.28-alpine", 1000);
  await slow(d, "kubectl rollout status deployment/web", 2000, 4);
  await say(d, "kubectl rollout history deployment/web", 4000);
  await say(d, "kubectl get deployment web -o wide", 5000);
  await say(d, "kubectl rollout undo deployment/web", 1000);
  await slow(d, "kubectl rollout status deployment/web", 1000, 4);
  await say(d, "kubectl get deployment web -o wide", 5000);

  await d.cue("drain");
  await d.clearScreen();
  await say(d, "kubectl scale deployment web --replicas=6", 1000);
  await slow(d, "kubectl rollout status deployment/web", 1000);
  await say(d, "kubectl get pods -o wide", 6000);
  await d.clearScreen();
  await slow(d, "kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data", 2500, 4);
  await offWait(d, "kubectl rollout status deployment/web --timeout=300s");
  await d.clearScreen();
  await say(d, "kubectl get pods -o wide", 6000);
  await say(d, "kubectl get nodes", 4000);
  await say(d, "kubectl uncordon k8s-lab-node2", 2500);
  off(d, "kubectl delete service web; kubectl delete deployment web");

  await d.cue("config");
  await d.clearScreen();
  await say(d, "kubectl create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=blue", 1000);
  await say(d, "kubectl create secret generic web-secret --from-literal=PASSWORD=labsecret", 1000);
  await say(d, "cat ~/k8s/env-demo.yaml", 7000);
  await say(d, "kubectl apply -f ~/k8s/env-demo.yaml", 500);
  await slow(d, "kubectl wait --for=condition=Ready pod/env-demo --timeout=180s", 1000);
  await d.clearScreen();
  await say(d, "kubectl logs env-demo", 4000);
  await say(d, "kubectl exec env-demo -- ls /config", 3500);
  await say(d, "kubectl exec env-demo -- cat /config/COLOR", 3500);

  await d.cue("secret");
  await d.clearScreen();
  await say(d, "kubectl get secret web-secret -o yaml", 5000);
  await say(d, "echo bGFic2VjcmV0 | base64 -d", 5000);
  off(d, "kubectl delete pod env-demo --grace-period=1; kubectl delete configmap web-config; kubectl delete secret web-secret");

  await d.cue("volume");
  await d.clearScreen();
  await slow(d, "microk8s enable hostpath-storage", 1500, 6);
  await d.clearScreen();
  await say(d, "kubectl create secret generic mariadb-root --from-literal=password=labroot", 1000);
  await say(d, "cat ~/k8s/mariadb.yaml", 9000);
  await say(d, "kubectl apply -f ~/k8s/mariadb.yaml", 500);
  await slow(d, "kubectl rollout status deployment/mariadb --timeout=600s", 1000, 8);
  await offWait(d, "until kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e 'SELECT 1'; do sleep 3; done");
  await d.clearScreen();
  await say(d, "kubectl get pvc", 5000);

  await d.cue("sql");
  await d.clearScreen();
  await typeLine(d, "kubectl exec -it deploy/mariadb -- mariadb -uroot -plabroot", 4000);
  await typeLine(d, "CREATE DATABASE shop;", 1500);
  await typeLine(d, "CREATE TABLE shop.items (name VARCHAR(20));", 1500);
  await typeLine(d, "INSERT INTO shop.items VALUES ('kept');", 2500);
  await say(d, "exit", 1500);
  await d.clearScreen();
  await say(d, "kubectl delete pod -l app=mariadb", 1500);
  await slow(d, "kubectl rollout status deployment/mariadb", 1000, 6);
  await offWait(d, "until kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -e 'SELECT 1'; do sleep 3; done");

  await d.cue("kept");
  await say(d, "kubectl get pods -o wide", 5000);
  await typeLine(d, "kubectl exec -it deploy/mariadb -- mariadb -uroot -plabroot", 4000);
  await typeLine(d, "SELECT * FROM shop.items;", 5000);
  await say(d, "exit", 1500);
  off(d, "kubectl delete deployment mariadb; kubectl delete pvc mariadb-data; kubectl delete secret mariadb-root");
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
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
