// Shared by the eight episodes of the Kubernetes course (tools/tour/clips/course-k8s-*.mjs,
// 2026-10-08): a slow series on the k8s-lab, three MicroK8s nodes. The course builds one small
// application in the namespace "shop": Redis for a shopping list, RustFS (an S3 store like MinIO)
// for notes and backups, and a Python app that says which pod, IP and node answered
// (vms/labs/k8s-lab/provision/manifests/shop/). Every episode starts from an empty namespace and
// rebuilds off camera what the previous episodes left (`prepare`), so each one can be recorded
// again on its own.
export const LAB = "k8s-lab";
export const VM = "k8s-lab-main";
export const CHECKOUT = "~/lab/demo/qemu-iso-lab";
export const APP = "http://127.0.0.1:8089/";                 // NodePort 30080, forwarded by the lab
export const CONSOLE = "http://127.0.0.1:8090/rustfs/console/"; // NodePort 30081

// The course's namespace gone, every node schedulable, the default namespace current again.
const RESET = `
kubectl delete namespace shop --ignore-not-found --wait=true --timeout=300s
for n in k8s-lab-main k8s-lab-node1 k8s-lab-node2; do kubectl uncordon $n >/dev/null; done
kubectl config set-context --current --namespace=default >/dev/null
microk8s enable hostpath-storage >/dev/null 2>&1
microk8s enable metrics-server >/dev/null 2>&1
cd ~/k8s/shop
`;

const wait = (deploy) => `kubectl rollout status deployment/${deploy} --timeout=300s`;

// The namespace as episode 1 leaves it (episode 2's demo-go is removed at its end).
export const AFTER_EPISODE_1 = `
kubectl create namespace shop
kubectl config set-context --current --namespace=shop
`;
// ...with Redis (episode 3)...
export const AFTER_EPISODE_3 = AFTER_EPISODE_1 + `
kubectl apply -f redis.yaml
${wait("redis")}
`;
// ...with RustFS and its Secret (episode 4)...
export const AFTER_EPISODE_4 = AFTER_EPISODE_3 + `
kubectl create secret generic s3-credentials --from-literal=access-key=labadmin --from-literal=secret-key=labsecret123
kubectl apply -f rustfs.yaml
${wait("rustfs")}
`;
// ...with the app and what episode 5 typed into it (episode 6 leaves it the same)...
export const AFTER_EPISODE_5 = AFTER_EPISODE_4 + `
kubectl create configmap shop-code --from-file=app.py --from-file=backup.py
kubectl apply -f shop.yaml
${wait("shop")}
sleep 5
for item in milk bread eggs; do curl -s -o /dev/null -X POST -d item=$item localhost:30080/add; done
curl -s -o /dev/null -X POST -d 'text=Remember the shopping bags' localhost:30080/note
`;
// ...and with the autoscaler of episode 7.
export const AFTER_EPISODE_7 = AFTER_EPISODE_5 + `
kubectl autoscale deployment shop --cpu-percent=50 --min=3 --max=9
`;

// Episode setup: the lab running, the manifests of the checkout copied onto the control plane,
// the namespace rebuilt, the browser blank, a clean terminal (small font: kubectl -o wide).
export async function courseSetup(d, prepare = "") {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl group up ${LAB} >/dev/null 2>&1; true`);
  d.vm(`cd ${CHECKOUT} && for i in $(seq 100); do ./bin/vmctl shell ${VM} -- "kubectl get nodes 2>/dev/null | grep -c ' Ready'" 2>/dev/null | grep -qx 3 && break; sleep 3; done; true`);
  d.vm(`cd ${CHECKOUT}/vms/labs/k8s-lab/provision/manifests && tar cz shop | ${CHECKOUT}/bin/vmctl shell ${VM} -- "mkdir -p ~/k8s && tar xz -C ~/k8s"`);
  // The prep must end on its marker: a take must not record an episode with nothing prepared.
  d.vm(`cat > /tmp/k8s-course-prep.sh <<'PREP'\n${RESET}\nset -e\n${prepare}\necho PREP-OK\nPREP`);
  for (let attempt = 1; ; attempt++) {
    const log = d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- "bash -s" < /tmp/k8s-course-prep.sh 2>&1 | tee /tmp/k8s-course-prep.log; true`);
    if (log.includes("PREP-OK")) break;
    if (attempt === 2) throw new Error(`the episode's prep failed twice (studio: /tmp/k8s-course-prep.log):\n${log.slice(-800)}`);
  }
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  // Logged out of the RustFS console (its token lives in the page's storage, not only in a
  // cookie): the login must happen on camera, as the stale cookie of the Proxmox GUI taught.
  await ctx.clearCookies();
  const cdp = await ctx.newCDPSession(d.page);
  await cdp.send("Storage.clearDataForOrigin", { origin: "http://127.0.0.1:8090", storageTypes: "all" });
  await cdp.detach();
  await d.page.goto("about:blank");
  await d.openTerminal(14);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// Onto the control plane, and (from episode 3 on) into the manifests' directory.
export async function enter(d, { manifests = true } = {}) {
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.session(VM);
  if (manifests) await d.guest("cd ~/k8s/shop", { read: 800 });
}

// The browser in front with `url` (reloaded when it is already open), for `ms`; then the terminal.
export async function browse(d, url, ms = 8000, during = null) {
  await d.focusBrowser();
  await d.page.bringToFront();
  if (d.page.url() === url) await d.page.reload();
  else await d.page.goto(url);
  await d.page.waitForLoadState("domcontentloaded");
  if (during) await during(d, d.page);
  await d.sleep(ms);
}
// The browser in front again as it was, no reload: the app's panel kept asking "who answers?"
// while the terminal was in front, so it shows what changed meanwhile.
export async function look(d, ms = 8000) {
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.sleep(ms);
}

// The RustFS console's login, typed where the viewer sees it, when the page asks for it.
export async function consoleLogin(d, page) {
  const account = page.locator("#accessKey");
  try { await account.waitFor({ timeout: 15000 }); } catch { return; }
  await d.click(account);
  await d.type("labadmin", 90);
  await d.click(page.locator("#secretKey"));
  await d.type("labsecret123", 90);
  await d.sleep(500);
  await d.click(page.locator("button[type=submit]", { hasText: "Login" }));
  await page.waitForURL(/\/browser\//, { timeout: 20000 });
  await d.sleep(1500);
}
// A bucket of the console opened with a click on its name.
export async function openBucket(d, page, name) {
  const link = page.locator(`a[href$="bucket=${name}"]`).first();
  await link.waitFor({ timeout: 20000 });
  await d.click(link);
  await page.waitForURL(new RegExp(`bucket=${name}`), { timeout: 20000 });
  await d.sleep(1500);
}
export async function backToTerminal(d) {
  await d.focusTerminal();
}

// Off camera, fast-forwarded: until the app's pods are exactly `count` and all Ready (a deleted pod
// still matches its label while it terminates, so `rollout status` alone can return too early).
export async function settle(d, app, count = 1) {
  d.ff(6);
  d.offCamera(VM, `for i in $(seq 90); do n=$(kubectl get pods -l app=${app} --no-headers 2>/dev/null | wc -l); [ "$n" = ${count} ] && kubectl wait --for=condition=Ready pod -l app=${app} --timeout=5s >/dev/null 2>&1 && break; sleep 2; done`);
  d.ffEnd();
}

// A slow course: every output stays on screen long enough to be read while the voice explains it.
export const say = (d, cmd, read = 6000) => d.guest(cmd, { read });
