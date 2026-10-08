// Kubernetes course, episode 7 of 8: probes, resources, and the autoscaler under load.
import { courseSetup, enter, say, settle, browse, look, backToTerminal, AFTER_EPISODE_5, APP } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 7/8 · Health and autoscaling", it: "Kubernetes 7/8 · Salute e autoscaling" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 7;

export const cues = [
  { id: "intro", en: "Episode seven: how Kubernetes knows a pod is healthy, how much it may consume, and how it adds pods by itself when the load grows.",
    it: "Settima puntata: come fa Kubernetes a sapere che un pod sta bene, quanto può consumare, e come aggiunge pod da solo quando il carico cresce." },
  { id: "probes", en: "Two probes in the manifest. Liveness asks: is the process alive? If not, the pod is restarted. Readiness asks: can it serve? For our app, ready means Redis answers. And the resources: what each pod requests, and its limit.",
    it: "Due sonde nel manifest. La liveness chiede: il processo è vivo? Se no, il pod viene riavviato. La readiness chiede: può servire? Per la nostra app, pronta vuol dire che Redis risponde. E le risorse: quanto chiede ogni pod, e il suo limite." },
  { id: "redis-down", en: "Turn Redis off: zero replicas. Within seconds the app's pods are running but not ready, zero of one: they leave the Service, which has no endpoints left.",
    it: "Spegniamo Redis: zero repliche. In pochi secondi i pod dell'app sono in esecuzione ma non pronti, zero su uno: escono dal Service, che resta senza endpoint." },
  { id: "redis-down-panel", en: "In the browser, nobody answers: better than an error page from half-working pods.",
    it: "Nel browser non risponde nessuno: meglio di una pagina d'errore da pod che funzionano a metà." },
  { id: "redis-up", en: "Redis back: the readiness probe passes again, the pods return to the Service on their own, and the list is still there, on Redis' volume.",
    it: "Redis riacceso: la sonda di readiness torna a passare, i pod rientrano da soli nel Service, e la lista è ancora lì, sul volume di Redis." },
  { id: "metrics", en: "To scale on load, the cluster must measure it: the metrics-server add-on. kubectl top shows CPU and memory, of nodes and of pods.",
    it: "Per scalare sul carico, il cluster deve misurarlo: l'add-on metrics-server. kubectl top mostra CPU e memoria, dei nodi e dei pod." },
  { id: "hpa", en: "A HorizontalPodAutoscaler: keep the app's average CPU at half of what it requests, with three to nine replicas.",
    it: "Un HorizontalPodAutoscaler: tenere la CPU media dell'app alla metà di quella richiesta, con da tre a nove repliche." },
  { id: "load", en: "And some load: two pods that ask the app for heavy work, without pause.",
    it: "E un po' di carico: due pod che chiedono all'app un lavoro pesante, senza sosta." },
  { id: "grow", en: "The autoscaler sees the CPU above target, and adds replicas: from three to nine, spread over the nodes.",
    it: "L'autoscaler vede la CPU sopra l'obiettivo, e aggiunge repliche: da tre a nove, distribuite sui nodi." },
  { id: "grow-panel", en: "In the panel, many more pods take turns.",
    it: "Nel pannello si alternano molti più pod." },
  { id: "calm", en: "Stop the load. The autoscaler waits a few minutes before removing pods, so that a short pause does not shrink the app too early.",
    it: "Fermiamo il carico. L'autoscaler aspetta qualche minuto prima di togliere pod, perché una pausa breve non rimpicciolisca l'app troppo presto." },
  { id: "end", top: true, en: "Last episode: a scheduled backup, where the data really live, and cleaning up.",
    it: "Nell'ultima puntata: un backup pianificato, dove stanno davvero i dati, e le pulizie." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_5);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(3000);
  await enter(d);

  await d.cue("probes");
  await say(d, "grep -A3 -E 'Probe|resources' shop.yaml", 14000);
  await browse(d, APP, 4000);
  await backToTerminal(d);

  await d.cue("redis-down");
  await d.clearScreen();
  await say(d, "kubectl scale deployment redis --replicas=0", 2000);
  d.ff(4);
  d.offCamera("k8s-lab-main", "for i in $(seq 40); do kubectl get pods -l app=shop --no-headers | grep -q ' 1/1 ' || break; sleep 1; done");
  d.ffEnd();
  await say(d, "kubectl get pods -l app=shop", 5000);
  await say(d, "kubectl get endpoints shop", 5000);
  await d.cue("redis-down-panel");
  await look(d, 8000);
  await backToTerminal(d);

  await d.cue("redis-up");
  await d.clearScreen();
  await say(d, "kubectl scale deployment redis --replicas=1", 2000);
  await settle(d, "redis");
  d.ff(4);
  d.offCamera("k8s-lab-main", "for i in $(seq 60); do [ $(kubectl get pods -l app=shop --no-headers | grep -c ' 1/1 ') = 3 ] && break; sleep 1; done");
  d.ffEnd();
  await say(d, "kubectl get pods -l app=shop", 4000);
  await say(d, "kubectl get endpoints shop", 4000);
  await look(d, 7000);
  await backToTerminal(d);

  await d.cue("metrics");
  await d.clearScreen();
  await say(d, "microk8s enable metrics-server", 3000);
  await say(d, "kubectl top nodes", 5000);
  await say(d, "kubectl top pods", 6000);

  await d.cue("hpa");
  await d.clearScreen();
  await say(d, "kubectl autoscale deployment shop --cpu-percent=50 --min=3 --max=9", 2500);
  await say(d, "kubectl get hpa", 5000);

  await d.cue("load");
  await say(d, "kubectl apply -f load.yaml", 4000);

  await d.cue("grow");
  d.ff(8);
  d.offCamera("k8s-lab-main", "for i in $(seq 120); do [ $(kubectl get deploy shop -o jsonpath='{.status.readyReplicas}') -ge 9 ] && break; sleep 2; done");
  d.ffEnd();
  await d.clearScreen();
  await say(d, "kubectl get hpa", 5000);
  await say(d, "kubectl get pods -l app=shop -o wide", 8000);
  await d.cue("grow-panel");
  await look(d, 12000);
  await backToTerminal(d);

  await d.cue("calm");
  await d.clearScreen();
  await say(d, "kubectl delete -f load.yaml", 3000);
  await say(d, "kubectl get hpa", 7000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
