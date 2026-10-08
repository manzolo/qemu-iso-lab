// Shared by the six episodes of the Git course (tools/tour/clips/course-git-*.mjs, 2026-10-08):
// a slow series on the git-lab, one topic per episode, all on one small repository, ~/first-repo
// (a shopping list, the one exercise 0 of the guide starts). Every episode starts from a clean home
// and rebuilds off camera the repository the previous episodes left (`prepare`), so each one can
// be recorded again on its own.
export const LAB = "git-lab";
export const VM = "git-lab-server";
export const CHECKOUT = "~/lab/demo/qemu-iso-lab";

// The course's repositories gone (the lab's own ~/workspace is not touched).
const RESET = `
cd ~
rm -rf ~/first-repo ~/first-remote.git ~/first-clone ~/colleague
`;

// ~/first-repo as episode 1 leaves it: four commits on main, list.txt and a .gitignore.
export const AFTER_EPISODE_1 = `
mkdir ~/first-repo
cd ~/first-repo
git init -q -b main
echo 'Shopping list' > list.txt
git add list.txt
git commit -q -m 'Start the shopping list'
printf 'milk\\nbread\\n' >> list.txt
git commit -q -am 'Add milk and bread'
echo 'eggs' >> list.txt
git commit -q -am 'Add eggs'
echo '*.tmp' > .gitignore
git add .gitignore
git commit -q -m 'Ignore temporary files'
`;

// ...and as episode 3 leaves it: the branches merged, a drinks file and the dinner menu.
export const AFTER_EPISODE_3 = AFTER_EPISODE_1 + `
echo 'cake' >> list.txt
git commit -q -am 'Weekend: cake'
echo 'balloons' >> list.txt
git commit -q -am 'Party: balloons'
echo 'Tea' > drinks.txt
git add drinks.txt
git commit -q -m 'Add drinks'
echo 'Dinner: pasta salad' > menu.txt
git add menu.txt
git commit -q -m 'Plan dinner'
`;

// ...and as episode 4 leaves it: a bare remote with main pushed, a colleague's clone whose
// commit has been pulled back.
export const AFTER_EPISODE_4 = AFTER_EPISODE_3 + `
echo 'Water' >> drinks.txt
git commit -q -am 'Add water'
echo 'butter' >> list.txt
git commit -q -am 'Add butter'
git init -q --bare ~/first-remote.git
git remote add origin ~/first-remote.git
git push -q -u origin main
git clone -q ~/first-remote.git ~/colleague
echo 'jam' >> ~/colleague/list.txt
git -C ~/colleague commit -q -am 'Add jam'
git -C ~/colleague push -q
git pull -q
`;

// ...and as episode 5 leaves it: a recipes file rebased onto main, the tag v1.0 pushed. The
// repository's own editor is `true` for episode 6: git flow's merges and tags would open one, and
// `true` keeps the message git prepared (local to the repository, gone with it at the next reset).
export const AFTER_EPISODE_5 = AFTER_EPISODE_4 + `
echo 'Coffee' >> drinks.txt
git commit -q -am 'Add coffee'
echo 'Pancakes: eggs, milk' > recipes.txt
git add recipes.txt
git commit -q -m 'Add recipes'
git tag -a v1.0 -m 'First complete list'
git push -q origin main v1.0
git config core.editor true
`;

// Episode setup: the lab installed and running, the course's repositories rebuilt, a clean terminal.
export async function courseSetup(d, prepare = "") {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl group install ${LAB} --yes >/dev/null 2>&1; true`);
  // A stack that has just started answers SSH a little later (same wait as the ZFS course).
  d.vm(`cd ${CHECKOUT} && for i in $(seq 60); do ./bin/vmctl shell ${VM} -- true >/dev/null 2>&1 && break; sleep 3; done; true`);
  // The prep must end on its marker: a take must not record an episode with nothing prepared.
  d.vm(`cat > /tmp/git-course-prep.sh <<'PREP'\n${RESET}\nset -e\n${prepare}\necho PREP-OK\nPREP`);
  for (let attempt = 1; ; attempt++) {
    const log = d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- "bash -s" < /tmp/git-course-prep.sh 2>&1 | tee /tmp/git-course-prep.log; true`);
    if (log.includes("PREP-OK")) break;
    if (attempt === 2) throw new Error(`the episode's prep failed twice (studio: /tmp/git-course-prep.log):\n${log.slice(-800)}`);
  }
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// Into the server, and (from episode 2 on) into the repository.
export async function enter(d, { repo = true } = {}) {
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.session(VM);
  if (repo) await d.guest("cd ~/first-repo", { read: 1000 });
}

// A slow course: every output stays on screen long enough to be read while the voice explains it.
export const say = (d, cmd, read = 6000) => d.guest(cmd, { read });
