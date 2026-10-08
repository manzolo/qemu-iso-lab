// Kubernetes course, episode 5 of 8: the app, from a ConfigMap, on three pods, in the browser.
import { courseSetup, enter, say, browse, backToTerminal, consoleLogin, openBucket, AFTER_EPISODE_4, APP, CONSOLE } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 5/8 · The application", it: "Kubernetes 5/8 · L'applicazione" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 5;

export const cues = [
  { id: "intro", en: "Episode five: the application. A small web app in Python: the list in Redis, the notes in RustFS, and on every page the pod that answered, its IP and its node.",
    it: "Quinta puntata: l'applicazione. Una piccola web app in Python: la lista in Redis, le note in RustFS, e in ogni pagina il pod che ha risposto, il suo IP e il suo nodo." },
  { id: "configmap", en: "No image to build: the code goes into a ConfigMap, and the pods run it on the standard Python image. A ConfigMap holds files or settings that are not secret.",
    it: "Nessuna immagine da costruire: il codice va in un ConfigMap, e i pod lo eseguono sull'immagine Python standard. Un ConfigMap contiene file o impostazioni che non sono segreti." },
  { id: "manifest", en: "The Deployment: three replicas; Redis and RustFS by their Service names; the keys from the Secret; and the downward API, which tells each pod its own IP and the node it runs on.",
    it: "Il Deployment: tre repliche; Redis e RustFS col nome dei loro Service; le chiavi dal Secret; e la downward API, che dice a ogni pod il suo IP e il nodo su cui gira." },
  { id: "pods", en: "Three pods of shop, spread over the three nodes, and a NodePort Service on 30080.",
    it: "Tre pod di shop, distribuiti sui tre nodi, e un Service NodePort sulla 30080." },
  { id: "browser", en: "In the browser. At the top, the pod that built this page. Below, a panel that asks every second who answers: three pods taking turns, each with its colour, its node and its IP.",
    it: "Nel browser. In alto, il pod che ha costruito questa pagina. Sotto, un pannello che chiede ogni secondo chi risponde: tre pod che si alternano, ognuno col suo colore, il suo nodo e il suo IP." },
  { id: "use", en: "Add a few things to the list, and a note. The list goes to Redis, the note becomes an object in RustFS; whichever pod answers, they all see the same data.",
    it: "Aggiungiamo qualcosa alla lista, e una nota. La lista va in Redis, la nota diventa un oggetto in RustFS; qualunque pod risponda, vedono tutti gli stessi dati." },
  { id: "bucket", en: "In the RustFS console, the app's bucket, notes, with the note as an object.",
    it: "Nella console di RustFS, il bucket dell'app, notes, con la nota come oggetto." },
  { id: "logs", en: "And the logs: kubectl logs with the app's label reads all three pods at once, each line prefixed with its pod.",
    it: "E i log: kubectl logs con l'etichetta dell'app legge tutti e tre i pod insieme, ogni riga col nome del suo pod." },
  { id: "end", top: true, en: "Next episode: scale the app, empty a node, and watch the pods move.",
    it: "Nella prossima puntata: scaliamo l'app, svuotiamo un nodo, e guardiamo i pod spostarsi." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_4);
}

// Types into one of the page's two forms and waits for the reloaded page to show the new entry
// (the first cut clicked the next field while the old page was still there: "bread" got lost).
async function add(d, page, field, text) {
  await d.click(page.locator(`input[name=${field}]`));
  await d.type(text, 80);
  await d.sleep(400);
  await d.key("Return");
  await page.locator("li", { hasText: text }).first().waitFor({ timeout: 20000 });
  await d.sleep(1500);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(3000);
  await enter(d);

  await d.cue("configmap");
  await say(d, "kubectl create configmap shop-code --from-file=app.py --from-file=backup.py", 3000);
  await say(d, "kubectl get configmap shop-code", 5000);

  await d.cue("manifest");
  await d.clearScreen();
  await say(d, "cat shop.yaml", 16000);

  await d.cue("pods");
  await d.clearScreen();
  await say(d, "kubectl apply -f shop.yaml", 3000);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/shop", 1000);
  d.ffEnd();
  await say(d, "kubectl get pods -l app=shop -o wide", 8000);

  await d.cue("browser");
  await browse(d, APP, 14000);

  await d.cue("use");
  const page = d.page;
  await add(d, page, "item", "milk");
  await add(d, page, "item", "bread");
  await add(d, page, "item", "eggs");
  await add(d, page, "text", "Remember the shopping bags");
  await d.sleep(5000);

  await d.cue("bucket");
  await browse(d, CONSOLE, 1000, async (d, page) => { await consoleLogin(d, page); await openBucket(d, page, "notes"); });
  await d.sleep(7000);
  await backToTerminal(d);

  await d.cue("logs");
  await d.clearScreen();
  await say(d, "kubectl logs -l app=shop --prefix --tail=4", 9000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
