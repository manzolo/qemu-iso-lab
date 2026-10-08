// Kubernetes course, episode 6 of 8: scale, drain a node, a rolling update, a rollback, with the
// app's "who answers" panel in the browser showing every move.
import { courseSetup, enter, say, settle, browse, look, backToTerminal, AFTER_EPISODE_5, APP } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 6/8 · Pods that move", it: "Kubernetes 6/8 · I pod che si spostano" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 6;

export const cues = [
  { id: "intro", en: "Episode six: pods that move. We keep an eye on the app's panel: who answers, from which node.",
    it: "Sesta puntata: i pod che si spostano. Teniamo d'occhio il pannello dell'app: chi risponde, e da quale nodo." },
  { id: "before", en: "Now three pods, one per node.",
    it: "Adesso tre pod, uno per nodo." },
  { id: "scale", en: "Scale to six replicas. Three new pods start, with new names and new IPs...",
    it: "Scaliamo a sei repliche. Partono tre pod nuovi, con nomi nuovi e IP nuovi..." },
  { id: "scale-panel", en: "...and in the panel, within seconds, the new pods answer too.",
    it: "...e nel pannello, in pochi secondi, rispondono anche i pod nuovi." },
  { id: "drain", en: "Maintenance on node two. Drain cordons it, so nothing new lands there, and evicts its pods: the Deployment recreates them on the other nodes.",
    it: "Manutenzione sul nodo due. Il drain lo isola, così non ci arriva niente di nuovo, e sfratta i suoi pod: il Deployment li ricrea sugli altri nodi." },
  { id: "drain-panel", en: "In the panel, node two has disappeared: only main and node one answer, and the app never stopped.",
    it: "Nel pannello, il nodo due è sparito: rispondono solo main e il nodo uno, e l'app non si è mai fermata." },
  { id: "uncordon", en: "Maintenance done: uncordon gives the node back to the scheduler. The pods already moved stay where they are; new ones may go there again.",
    it: "Manutenzione finita: uncordon restituisce il nodo allo scheduler. I pod già spostati restano dove sono; quelli nuovi potranno tornarci." },
  { id: "update", en: "A new version: a different title and colour, set as environment variables. Every change of the pod template is a rolling update: new pods come up while the old ones go away, a few at a time.",
    it: "Una nuova versione: titolo e colore diversi, impostati come variabili d'ambiente. Ogni cambio del modello dei pod è un rolling update: i pod nuovi salgono mentre i vecchi se ne vanno, pochi alla volta." },
  { id: "update-panel", en: "The page shows the new version, and the panel the new pods.",
    it: "La pagina mostra la versione nuova, e il pannello i pod nuovi." },
  { id: "undo", en: "Not convinced? Every rolling update is a revision, and rollout undo goes back to the previous one.",
    it: "Non convince? Ogni rolling update è una revisione, e rollout undo torna alla precedente." },
  { id: "end", top: true, en: "Next episode: health checks, resources, and the autoscaler under load.",
    it: "Nella prossima puntata: i controlli di salute, le risorse, e l'autoscaler sotto carico." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_5);
}

export async function run(d) {
  await d.cue("intro");
  await browse(d, APP, 6000);
  await backToTerminal(d);
  await enter(d);

  await d.cue("before");
  await say(d, "kubectl get pods -l app=shop -o wide", 6000);

  await d.cue("scale");
  await d.clearScreen();
  await say(d, "kubectl scale deployment shop --replicas=6", 2500);
  await settle(d, "shop", 6);
  await say(d, "kubectl get pods -l app=shop -o wide", 7000);
  await d.cue("scale-panel");
  await look(d, 11000);
  await backToTerminal(d);

  await d.cue("drain");
  await d.clearScreen();
  await say(d, "kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data", 3000);
  await settle(d, "shop", 6);
  await say(d, "kubectl get nodes", 4000);
  await say(d, "kubectl get pods -l app=shop -o wide", 7000);
  await d.cue("drain-panel");
  await look(d, 11000);
  await backToTerminal(d);

  await d.cue("uncordon");
  await d.clearScreen();
  await say(d, "kubectl uncordon k8s-lab-node2", 3000);
  await say(d, "kubectl get nodes", 6000);

  await d.cue("update");
  await d.clearScreen();
  await say(d, "kubectl set env deployment/shop TITLE='Weekend shopping' COLOR=purple", 2500);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/shop", 1000);
  d.ffEnd();
  await settle(d, "shop", 6);
  await say(d, "kubectl get pods -l app=shop -o wide", 6000);
  await d.cue("update-panel");
  await browse(d, APP, 10000);
  await backToTerminal(d);

  await d.cue("undo");
  await d.clearScreen();
  await say(d, "kubectl rollout history deployment/shop", 5000);
  await say(d, "kubectl rollout undo deployment/shop", 2500);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/shop", 1000);
  d.ffEnd();
  await settle(d, "shop", 6);
  await browse(d, APP, 7000);
  await backToTerminal(d);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
