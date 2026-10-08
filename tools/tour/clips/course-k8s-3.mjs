// Kubernetes course, episode 3 of 8: Redis, a volume claim, a Service name in the cluster's DNS.
import { courseSetup, enter, say, settle, AFTER_EPISODE_1 } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 3/8 · Redis and volumes", it: "Kubernetes 3/8 · Redis e i volumi" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 3;

export const cues = [
  { id: "intro", en: "Episode three: the first piece of the application, Redis, a fast in-memory database. It will hold the shopping list.",
    it: "Terza puntata: il primo pezzo dell'applicazione, Redis, un database in memoria molto veloce. Terrà la lista della spesa." },
  { id: "manifest", en: "This time no long commands: a manifest, a YAML file that describes the objects. Three of them: a PersistentVolumeClaim, a request for a little disk; a Deployment with one Redis replica, pinned to the control plane; and a Service called redis.",
    it: "Questa volta niente comandi lunghi: un manifest, un file YAML che descrive gli oggetti. Tre: un PersistentVolumeClaim, la richiesta di un po' di disco; un Deployment con una replica di Redis, fissata sul control plane; e un Service che si chiama redis." },
  { id: "apply", en: "kubectl apply hands the file to the cluster, which creates what is missing. Apply it again later, and only what changed is updated.",
    it: "kubectl apply consegna il file al cluster, che crea quello che manca. Riapplicalo più tardi, e si aggiorna solo quello che è cambiato." },
  { id: "pvc", en: "The claim is Bound: the hostpath-storage add-on gave it a directory on the node's disk. Redis writes its data there, so they survive the pod.",
    it: "Il claim è Bound: l'add-on hostpath-storage gli ha dato una directory sul disco del nodo. Redis ci scrive i suoi dati, così sopravvivono al pod." },
  { id: "dns", en: "Other pods find Redis by the Service's name. A throwaway pod with redis-cli asks the host called redis: PONG. The cluster's DNS turned the name into the Service's address.",
    it: "Gli altri pod trovano Redis col nome del Service. Un pod usa e getta con redis-cli chiede all'host che si chiama redis: PONG. Il DNS del cluster ha trasformato il nome nell'indirizzo del Service." },
  { id: "data", en: "kubectl exec runs a command inside the running pod: write a key, read it back.",
    it: "kubectl exec esegue un comando dentro il pod in esecuzione: scriviamo una chiave, la rileggiamo." },
  { id: "survive", en: "Delete the pod. The Deployment makes a new one, which mounts the same volume: the key is still there.",
    it: "Cancelliamo il pod. Il Deployment ne crea uno nuovo, che monta lo stesso volume: la chiave c'è ancora." },
  { id: "end", top: true, en: "Next episode: an object store for files, RustFS, and a Secret for its keys.",
    it: "Nella prossima puntata: un object storage per i file, RustFS, e un Secret per le sue chiavi." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_1);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2500);
  await enter(d);
  await say(d, "ls", 3000);

  await d.cue("manifest");
  await d.clearScreen();
  await say(d, "cat redis.yaml", 16000);

  await d.cue("apply");
  await d.clearScreen();
  await say(d, "kubectl apply -f redis.yaml", 3000);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/redis", 1000);
  d.ffEnd();
  await say(d, "kubectl get pods -o wide", 5000);

  await d.cue("pvc");
  await say(d, "kubectl get pvc", 7000);

  await d.cue("dns");
  await d.clearScreen();
  await say(d, "kubectl get service redis", 4000);
  await say(d, "kubectl run redis-test --image=redis:7-alpine --rm -i --restart=Never -- redis-cli -h redis ping", 7000);

  await d.cue("data");
  await d.clearScreen();
  await say(d, "kubectl exec deploy/redis -- redis-cli SET greeting hello", 3000);
  await say(d, "kubectl exec deploy/redis -- redis-cli GET greeting", 5000);

  await d.cue("survive");
  await d.clearScreen();
  await say(d, "kubectl delete pod -l app=redis", 3000);
  await settle(d, "redis");
  await say(d, "kubectl get pods", 4000);
  await say(d, "kubectl exec deploy/redis -- redis-cli GET greeting", 6000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
