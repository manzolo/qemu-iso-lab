// Kubernetes course, episode 8 of 8: a CronJob backing Redis up into RustFS, where the data live,
// and the namespace deleted.
import { courseSetup, enter, say, browse, backToTerminal, consoleLogin, openBucket, AFTER_EPISODE_7, CONSOLE } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 8/8 · Backups and data", it: "Kubernetes 8/8 · Backup e dati" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 8;

export const cues = [
  { id: "intro", en: "Last episode: the three pieces working together. A scheduled backup takes Redis' data and stores it in RustFS.",
    it: "Ultima puntata: i tre pezzi che lavorano insieme. Un backup pianificato prende i dati di Redis e li salva in RustFS." },
  { id: "cronjob", en: "A CronJob runs a Job on a schedule, written like cron: here every two minutes. In the Job, an init container asks Redis for a snapshot; then the main container uploads it to a bucket called backups, with the app's own S3 code.",
    it: "Un CronJob esegue un Job a orari fissi, scritti come in cron: qui ogni due minuti. Nel Job, un init container chiede a Redis una fotografia dei dati; poi il container principale la carica in un bucket che si chiama backups, col codice S3 dell'app stessa." },
  { id: "now", en: "No need to wait for the schedule: create a Job from the CronJob right now, and wait until it completes.",
    it: "Non serve aspettare l'orario: creiamo subito un Job dal CronJob, e aspettiamo che finisca." },
  { id: "logs", en: "Its log: the object it wrote, and its size. The Job's pod stays, Completed, so that its log can still be read.",
    it: "Il suo log: l'oggetto che ha scritto, e la sua dimensione. Il pod del Job resta, Completed, così il suo log si può ancora leggere." },
  { id: "console", en: "In the RustFS console: the backups bucket, with Redis' snapshot.",
    it: "Nella console di RustFS: il bucket backups, con la fotografia di Redis." },
  { id: "pv", en: "Where do the data really live? Each claim is bound to a volume, and with hostpath storage a volume is a directory on one node's disk. That is why Redis and RustFS are pinned to the control plane: they could not follow a drain to another node. A real cluster uses network storage, Ceph or a cloud disk, which any node can mount.",
    it: "Dove stanno davvero i dati? Ogni claim è legato a un volume, e con lo storage hostpath un volume è una directory sul disco di un nodo. Per questo Redis e RustFS sono fissati sul control plane: non potrebbero seguire un drain su un altro nodo. Un cluster vero usa uno storage di rete, Ceph o un disco cloud, che qualunque nodo può montare." },
  { id: "all", en: "The whole application, in one list: Deployments, ReplicaSets, pods, Services, the autoscaler, the CronJob.",
    it: "Tutta l'applicazione, in un elenco: Deployment, ReplicaSet, pod, Service, l'autoscaler, il CronJob." },
  { id: "delete", en: "And when it is no longer needed, one command: deleting the namespace deletes everything inside it, volumes included.",
    it: "E quando non serve più, un comando: cancellare il namespace cancella tutto quello che contiene, volumi compresi." },
  { id: "recap", en: "Eight episodes: the cluster, pods on the nodes, Redis and volumes, object storage and Secrets, the app from a ConfigMap, pods that move, health and autoscaling, backups and data.",
    it: "Otto puntate: il cluster, i pod sui nodi, Redis e i volumi, object storage e Secret, l'app da un ConfigMap, i pod che si spostano, salute e autoscaling, backup e dati." },
  { id: "end", top: true, en: "Every manifest of the course is in the lab, and the guide has every command, in English and Italian, from the lab's card.",
    it: "Ogni manifest del corso è nel lab, e la guida ha ogni comando, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_7);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(3000);
  await enter(d);

  await d.cue("cronjob");
  await say(d, "cat backup.yaml", 14000);
  await d.clearScreen();
  await say(d, "kubectl apply -f backup.yaml", 2500);
  await say(d, "kubectl get cronjob", 5000);

  await d.cue("now");
  await d.clearScreen();
  await say(d, "kubectl create job --from=cronjob/redis-backup backup-now", 2500);
  d.ff(4);
  await say(d, "kubectl wait --for=condition=Complete job/backup-now --timeout=180s", 1500);
  d.ffEnd();

  await d.cue("logs");
  await say(d, "kubectl logs job/backup-now", 5000);
  await say(d, "kubectl get pods -l job-name=backup-now", 5000);

  await d.cue("console");
  await browse(d, CONSOLE, 1000, async (d, page) => { await consoleLogin(d, page); await openBucket(d, page, "backups"); });
  await d.sleep(7000);
  await backToTerminal(d);

  await d.cue("pv");
  await d.clearScreen();
  await say(d, "kubectl get pvc", 5000);
  await say(d, "kubectl get pods -l 'app in (redis,rustfs)' -o wide", 12000);

  await d.cue("all");
  await d.clearScreen();
  await say(d, "kubectl get all", 12000);

  await d.cue("delete");
  await d.clearScreen();
  d.ff(6);
  await say(d, "kubectl delete namespace shop", 1000);
  d.ffEnd();
  await say(d, "kubectl get pv", 5000);

  await d.cue("recap");
  await say(d, "kubectl get nodes", 9000);
  await d.leave();

  await d.cue("end");
  await d.sleep(6000);
}
