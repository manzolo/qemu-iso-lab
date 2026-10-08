export const title = { en: "Docker from the first container: the Docker lab", it: "Docker dal primo container: il lab Docker" };
export const series = "labs";
export const lab = "docker-lab";

const LAB = "docker-lab";
const VM = "docker-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "A container is a process with its own file system, network and limits, started from an image in a second. The Docker lab is one server with Docker Engine and Compose, and two small images already pulled.",
    it: "Un container è un processo con il suo file system, la sua rete e i suoi limiti, avviato da un'immagine in un secondo. Il lab Docker è un server con Docker Engine e Compose, e due piccole immagini già scaricate." },
  { id: "install", en: "One cloud image; the install also pulls the images, so the exercises need no network.",
    it: "Una cloud image; l'installazione scarica anche le immagini, così gli esercizi non hanno bisogno della rete." },
  { id: "anatomy", en: "The engine is a daemon the docker command talks to over a socket. The lab user is in the docker group: that is why no sudo is needed.",
    it: "Il motore è un demone con cui il comando docker parla su un socket. L'utente del lab è nel gruppo docker: ecco perché non serve sudo." },
  { id: "images", en: "An image is the read-only template, made of layers: history shows how alpine was built.",
    it: "Un'immagine è il modello in sola lettura, fatto a strati: history mostra come è stata costruita alpine." },
  { id: "run", en: "docker run starts a container from it. With --rm it is removed when the command ends; with -d it stays up: here nginx, its port 80 published on 8080.",
    it: "docker run ne avvia un container. Con --rm viene rimosso quando il comando finisce; con -d resta su: qui nginx, con la porta 80 pubblicata sulla 8080." },
  { id: "inside", en: "exec runs a second process inside, logs reads what the main one printed, port shows how it is published. rm -f stops and removes it.",
    it: "exec esegue un secondo processo dentro, logs legge cosa ha stampato il principale, port mostra come è pubblicato. rm -f lo ferma e lo rimuove." },
  { id: "volumes", en: "A container's own layer disappears with it. A named volume survives: one container writes, a second one reads.",
    it: "Lo strato proprio di un container sparisce con lui. Un volume con nome sopravvive: un container scrive, un secondo legge." },
  { id: "compose", en: "Compose describes several containers in one file: image, ports, volumes. up -d brings them up together, down takes them away.",
    it: "Compose descrive più container in un solo file: immagine, porte, volumi. up -d li tira su insieme, down li toglie." },
  { id: "build", en: "And your own image: a Dockerfile, each instruction a layer. build it, run it, remove it.",
    it: "E un'immagine tua: un Dockerfile, ogni istruzione uno strato. build, run, poi via." },
  { id: "tests", en: "Clean again, the lab's six tests repeat every step and tell you the engine still behaves.",
    it: "Di nuovo pulito, i sei test del lab ripetono ogni passo e ti dicono che il motore si comporta ancora bene." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${VM} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// Rewritten 2026-10-07 in the approved style (docs/TOUR.md, "How a lesson types its commands"):
// one short command per step, the files of compose and build written off camera and shown with
// cat, the cleanups off camera.
const say = (d, cmd, read = 3500) => d.guest(cmd, { read });
const off = (d, cmd) => d.offCamera(VM, cmd);
const COMPOSE = `mkdir -p ~/compose-demo/html && echo '<h1>hello from compose</h1>' > ~/compose-demo/html/index.html && printf 'services:\\n  web:\\n    image: nginx:1.27-alpine\\n    pull_policy: never\\n    ports:\\n      - "8081:80"\\n    volumes:\\n      - ./html:/usr/share/nginx/html:ro\\n' > ~/compose-demo/compose.yaml`;
const DOCKERFILE = `mkdir -p ~/build-demo && printf 'FROM alpine:3.20\\nRUN echo built-in-the-lab > /message\\nCMD ["cat", "/message"]\\n' > ~/build-demo/Dockerfile`;

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(3500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(16);
  for (let i = 0; i < 1200 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.clearScreen();
  await d.session(VM);
  await say(d, "systemctl is-active docker", 2000);
  await say(d, "docker version", 5000);
  await say(d, "groups", 3500);

  await d.cue("images");
  await d.clearScreen();
  await say(d, "docker image ls", 3500);
  await say(d, "docker image history alpine:3.20", 4500);

  await d.cue("run");
  await d.clearScreen();
  await say(d, "docker run --rm alpine:3.20 cat /etc/os-release", 3500);
  await say(d, "docker run -d --name lab-web -p 8080:80 nginx:1.27-alpine", 2000);
  await say(d, "docker ps", 3500);
  await say(d, "curl -sI localhost:8080", 3500);

  await d.cue("inside");
  await d.clearScreen();
  await say(d, "docker exec lab-web nginx -v", 2500);
  await say(d, "docker logs lab-web", 3500);
  await say(d, "docker port lab-web", 3000);
  await say(d, "docker rm -f lab-web", 2000);

  await d.cue("volumes");
  await d.clearScreen();
  await say(d, "docker volume create lab-data", 1500);
  await say(d, "docker run --rm -v lab-data:/data alpine:3.20 touch /data/kept", 2000);
  await say(d, "docker run --rm -v lab-data:/data alpine:3.20 ls /data", 3500);
  await say(d, "docker volume rm lab-data", 1500);

  await d.cue("compose");
  off(d, COMPOSE);
  await d.clearScreen();
  await say(d, "cd ~/compose-demo", 500);
  await say(d, "cat compose.yaml", 5000);
  await say(d, "docker compose up -d", 2500);
  await say(d, "docker compose ps", 3500);
  await say(d, "curl -s localhost:8081", 3500);
  await say(d, "docker compose down", 2500);
  await say(d, "cd", 300);
  off(d, "rm -rf ~/compose-demo");

  await d.cue("build");
  off(d, DOCKERFILE);
  await d.clearScreen();
  await say(d, "cd ~/build-demo", 500);
  await say(d, "cat Dockerfile", 4000);
  await d.guest("docker build -t lab-hello .", { read: 3000, timeout: 120000 });
  await say(d, "docker run --rm lab-hello", 3500);
  await say(d, "cd", 300);
  off(d, "docker image rm lab-hello; rm -rf ~/build-demo");
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
