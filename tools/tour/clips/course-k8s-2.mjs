// Kubernetes course, episode 2 of 8: a Deployment, a Service, pods spread and reborn. The image is
// manzolo/demo-go: it answers with the pod's name, IP and the time.
import { courseSetup, enter, say, browse, backToTerminal, AFTER_EPISODE_1, APP } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 2/8 · Pods on the nodes", it: "Kubernetes 2/8 · I pod sui nodi" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 2;

export const cues = [
  { id: "intro", en: "Episode two: a first Deployment. The image is a tiny web service, demo-go: to every request it answers with the name and the IP of the pod that served it.",
    it: "Seconda puntata: un primo Deployment. L'immagine è un piccolo servizio web, demo-go: a ogni richiesta risponde col nome e l'IP del pod che l'ha servita." },
  { id: "deploy", en: "A Deployment keeps a number of identical pods alive: here three. Kubernetes downloads the image and starts them.",
    it: "Un Deployment tiene in vita un certo numero di pod identici: qui tre. Kubernetes scarica l'immagine e li avvia." },
  { id: "spread", en: "With -o wide: every pod has its own IP, and the scheduler has put them on different nodes.",
    it: "Con o wide: ogni pod ha il suo IP, e lo scheduler li ha messi su nodi diversi." },
  { id: "service", en: "Pods come and go, their IPs change. A Service gives them one stable address. This one is a NodePort: port 30080, open on every node.",
    it: "I pod vanno e vengono, i loro IP cambiano. Un Service dà loro un indirizzo stabile. Questo è un NodePort: la porta 30080, aperta su ogni nodo." },
  { id: "curl", en: "Ask it a few times: each answer comes from a different pod, with its own name and IP. The Service spreads the requests.",
    it: "Chiediamoglielo qualche volta: ogni risposta arriva da un pod diverso, col suo nome e il suo IP. Il Service distribuisce le richieste." },
  { id: "browser", en: "The lab forwards that port to the host: the same service in the browser. Reload, and the pod changes.",
    it: "Il lab inoltra quella porta all'host: lo stesso servizio nel browser. Ricarichi, e il pod cambia." },
  { id: "scale", en: "Six replicas instead of three: three new pods, on the nodes with room for them.",
    it: "Sei repliche invece di tre: tre pod nuovi, sui nodi che hanno posto." },
  { id: "reborn", en: "Now delete every pod of the Deployment. It does not give up: new pods are born at once, with new names and new IPs, maybe on other nodes. The Deployment wants six, and six there will be.",
    it: "Ora cancelliamo tutti i pod del Deployment. Non si arrende: nascono subito pod nuovi, con nomi nuovi e IP nuovi, magari su altri nodi. Il Deployment ne vuole sei, e sei saranno." },
  { id: "cleanup", en: "The demo has done its job: delete the Deployment and the Service. The namespace is empty again, ready for the real application.",
    it: "La demo ha fatto il suo lavoro: cancelliamo Deployment e Service. Il namespace torna vuoto, pronto per l'applicazione vera." },
  { id: "end", top: true, en: "Next episode: Redis, the first piece of the application, with its data on a volume.",
    it: "Nella prossima puntata: Redis, il primo pezzo dell'applicazione, con i dati su un volume." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_1);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2500);
  await enter(d, { manifests: false });

  await d.cue("deploy");
  await say(d, "kubectl create deployment hello --image=manzolo/demo-go:0.2.0 --replicas=3", 2000);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/hello", 1000);
  d.ffEnd();
  await d.cue("spread");
  await say(d, "kubectl get pods -o wide", 9000);

  await d.cue("service");
  await d.clearScreen();
  await say(d, "kubectl create service nodeport hello --tcp=80:8080 --node-port=30080", 2500);
  await say(d, "kubectl get service hello", 6000);

  await d.cue("curl");
  await d.clearScreen();
  await say(d, "curl -s localhost:30080", 3500);
  await say(d, "curl -s localhost:30080", 3500);
  await say(d, "curl -s localhost:30080", 5000);

  await d.cue("browser");
  await browse(d, APP, 3500);
  for (let i = 0; i < 2; i++) { await d.page.reload(); await d.sleep(3500); }
  await backToTerminal(d);

  await d.cue("scale");
  await d.clearScreen();
  await say(d, "kubectl scale deployment hello --replicas=6", 4000);
  await say(d, "kubectl get pods -o wide", 8000);

  await d.cue("reborn");
  await d.clearScreen();
  await say(d, "kubectl delete pods -l app=hello", 3000);
  await say(d, "kubectl get pods -o wide", 9000);

  await d.cue("cleanup");
  await d.clearScreen();
  await say(d, "kubectl delete deployment hello", 2500);
  await say(d, "kubectl delete service hello", 2500);
  await say(d, "kubectl get all", 5000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
