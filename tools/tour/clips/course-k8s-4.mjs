// Kubernetes course, episode 4 of 8: RustFS, an S3 object store, with its keys in a Secret.
import { courseSetup, enter, say, browse, backToTerminal, consoleLogin, AFTER_EPISODE_3, CONSOLE } from "../k8s-course.mjs";

export const title = { en: "Kubernetes 4/8 · Object storage and Secrets", it: "Kubernetes 4/8 · Object storage e Secret" };
export const series = "courses";
export const lab = "k8s-lab";
export const order = 4;

export const cues = [
  { id: "intro", en: "Episode four: where the application keeps its files. An object store speaks S3, the protocol of Amazon's storage: buckets and objects over HTTP. The best known one to run yourself was MinIO; in 2025 it stopped publishing its images, so we use RustFS, which speaks the same S3.",
    it: "Quarta puntata: dove l'applicazione tiene i suoi file. Un object storage parla S3, il protocollo dello storage di Amazon: bucket e oggetti via HTTP. Il più noto da installare in casa era MinIO; nel 2025 ha smesso di pubblicare le sue immagini, quindi usiamo RustFS, che parla lo stesso S3." },
  { id: "secret", en: "First the keys. A Secret holds credentials apart from the manifests: here an access key and a secret key.",
    it: "Prima le chiavi. Un Secret tiene le credenziali separate dai manifest: qui una access key e una secret key." },
  { id: "base64", en: "Careful: a Secret is only encoded in base64, not encrypted. Whoever may read Secrets in the namespace reads the password.",
    it: "Attenzione: un Secret è solo codificato in base64, non cifrato. Chi può leggere i Secret del namespace legge la password." },
  { id: "apply", en: "The RustFS manifest takes the two keys from the Secret as environment variables, asks for a one-gigabyte volume, and publishes two Services: the S3 API inside the cluster, and the web console on NodePort 30081.",
    it: "Il manifest di RustFS prende le due chiavi dal Secret come variabili d'ambiente, chiede un volume da un gigabyte, e pubblica due Service: l'API S3 dentro il cluster, e la console web sul NodePort 30081." },
  { id: "pods", en: "Redis and RustFS run side by side on the control plane, each with its claim.",
    it: "Redis e RustFS girano fianco a fianco sul control plane, ognuno col suo claim." },
  { id: "console", en: "The lab forwards the console to the host. We log in with the keys of the Secret: no bucket yet. The application will create its own.",
    it: "Il lab inoltra la console all'host. Entriamo con le chiavi del Secret: ancora nessun bucket. L'applicazione creerà il suo." },
  { id: "end", top: true, en: "Next episode: the application itself, on three pods, and in the browser.",
    it: "Nella prossima puntata: l'applicazione vera e propria, su tre pod, e nel browser." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_3);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(4000);
  await enter(d);

  await d.cue("secret");
  await say(d, "kubectl create secret generic s3-credentials --from-literal=access-key=labadmin --from-literal=secret-key=labsecret123", 2500);
  await say(d, "kubectl get secret s3-credentials -o yaml", 6000);
  await d.cue("base64");
  await say(d, "echo bGFic2VjcmV0MTIz | base64 -d", 6000);

  await d.cue("apply");
  await d.clearScreen();
  await say(d, "kubectl apply -f rustfs.yaml", 3000);
  d.ff(4);
  await say(d, "kubectl rollout status deployment/rustfs", 1000);
  d.ffEnd();
  await say(d, "kubectl get services", 7000);

  await d.cue("pods");
  await d.clearScreen();
  await say(d, "kubectl get pods -o wide", 5000);
  await say(d, "kubectl get pvc", 6000);

  await d.cue("console");
  await browse(d, CONSOLE, 2000, consoleLogin);
  await d.sleep(6000);
  await backToTerminal(d);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
