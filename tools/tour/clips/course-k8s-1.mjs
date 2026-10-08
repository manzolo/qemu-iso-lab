// Kubernetes course, episode 1 of 8: the cluster, kubectl, a namespace (tools/tour/k8s-course.mjs).
import { courseSetup, enter, say } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 1/8 · The cluster", it: "Kubernetes 1/8 · Il cluster" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 1;

export const cues = [
  { id: "intro", en: "A Kubernetes course in eight episodes, on the Kubernetes lab: three virtual machines running MicroK8s. Episode by episode we build a small application, a shopping list with Redis and an object store, and we watch its pods move around the cluster.",
    it: "Un corso su Kubernetes in otto puntate, sul lab Kubernetes: tre macchine virtuali con MicroK8s. Puntata dopo puntata costruiamo una piccola applicazione, una lista della spesa con Redis e un object storage, e guardiamo i suoi pod spostarsi nel cluster." },
  { id: "nodes", en: "We work on the control plane. kubectl get nodes: three nodes, all Ready. With -o wide, each one's internal address on the lab network, and the operating system.",
    it: "Lavoriamo sul control plane. kubectl get nodes: tre nodi, tutti Ready. Con o wide, l'indirizzo interno di ognuno sulla rete del lab, e il sistema operativo." },
  { id: "system", en: "The cluster runs on pods too. In the kube-system namespace: calico-node, the network, one per node; CoreDNS, the cluster's DNS; and the other pieces of the control plane's add-ons.",
    it: "Anche il cluster gira su dei pod. Nel namespace kube-system: calico-node, la rete, uno per nodo; CoreDNS, il DNS del cluster; e gli altri pezzi aggiuntivi del control plane." },
  { id: "namespace", en: "A namespace is a room of the cluster: names, permissions and quotas stay inside it. Our application gets its own, called shop.",
    it: "Un namespace è una stanza del cluster: nomi, permessi e quote restano lì dentro. La nostra applicazione ne ha uno suo, che si chiama shop." },
  { id: "context", en: "So that we do not have to write it on every command, we make shop the current namespace. For now it is empty.",
    it: "Per non doverlo scrivere in ogni comando, rendiamo shop il namespace corrente. Per ora è vuoto." },
  { id: "api", en: "Everything kubectl does is a request to the API server. api-resources lists the kinds of objects it knows: pods, services, deployments, and many more. We will meet the most important ones.",
    it: "Tutto quello che fa kubectl è una richiesta all'API server. api-resources elenca i tipi di oggetti che conosce: pod, service, deployment, e molti altri. Incontreremo i più importanti." },
  { id: "end", top: true, en: "Next episode: a first Deployment, and pods that spread over the nodes.",
    it: "Nella prossima puntata: un primo Deployment, e i pod che si distribuiscono sui nodi." },
];

export async function setup(d) {
  await courseSetup(d);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(3000);
  await enter(d, { manifests: false });

  await d.cue("nodes");
  await say(d, "kubectl get nodes", 4000);
  await say(d, "kubectl get nodes -o wide", 8000);

  await d.cue("system");
  await d.clearScreen();
  await say(d, "kubectl get pods -n kube-system -o wide", 10000);

  await d.cue("namespace");
  await d.clearScreen();
  await say(d, "kubectl get namespaces", 4000);
  await say(d, "kubectl create namespace shop", 3000);

  await d.cue("context");
  await say(d, "kubectl config set-context --current --namespace=shop", 2500);
  await say(d, "kubectl get pods", 5000);

  await d.cue("api");
  await d.clearScreen();
  await say(d, "kubectl api-resources --namespaced=true", 9000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
