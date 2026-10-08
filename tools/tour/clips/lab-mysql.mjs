export const title = { en: "SQL on a real server: the MySQL lab", it: "SQL su un server vero: il lab MySQL" };
export const series = "labs";
export const lab = "mysql-lab";

const LAB = "mysql-lab";
const VM = "mysql-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "A database is easier to understand once you have queried a real one. The MySQL lab is one server with MySQL 8, a small shop database already loaded, and phpMyAdmin to see it in the browser.",
    it: "Un database si capisce meglio dopo averne interrogato uno vero. Il lab MySQL è un server con MySQL 8, un piccolo database di un negozio già caricato, e phpMyAdmin per vederlo nel browser." },
  { id: "install", en: "One cloud image; the install adds MySQL, Apache and phpMyAdmin, and loads the sample data.",
    it: "Una cloud image; l'installazione aggiunge MySQL, Apache e phpMyAdmin, e carica i dati di esempio." },
  { id: "anatomy", en: "The service runs, and root logs in through the socket with no password. testdb holds two tables, users and orders; the lab account reads its password from my.cnf.",
    it: "Il servizio gira, e root entra dal socket senza password. testdb contiene due tabelle, users e orders; l'account del lab legge la sua password da my.cnf." },
  { id: "queries", en: "SELECT reads, WHERE filters, ORDER BY sorts: the most expensive order first.",
    it: "SELECT legge, WHERE filtra, ORDER BY ordina: prima l'ordine più caro." },
  { id: "joins", en: "JOIN matches each order to its user. LEFT JOIN keeps every user, even Dana, who never ordered: NULL where nothing matches. GROUP BY folds the orders into totals.",
    it: "JOIN abbina ogni ordine al suo utente. LEFT JOIN tiene tutti gli utenti, anche Dana, che non ha mai ordinato: NULL dove non c'è corrispondenza. GROUP BY riduce gli ordini a totali." },
  { id: "transactions", en: "Inside a transaction the new row is there, but nothing is final: ROLLBACK takes it back. And the foreign key refuses to delete a user who still has orders.",
    it: "Dentro una transazione la nuova riga c'è, ma niente è definitivo: ROLLBACK la annulla. E la chiave esterna rifiuta di cancellare un utente che ha ancora degli ordini." },
  { id: "privileges", en: "An account gets exactly what it needs: reader may SELECT from testdb and nothing else. The INSERT is denied.",
    it: "Un account riceve esattamente ciò che gli serve: reader può fare SELECT su testdb e nient'altro. L'INSERT viene negato." },
  { id: "admin", en: "A second database, testlab, with a students table. EXPLAIN shows how a query runs: every row read before the index, straight to the match after it.",
    it: "Un secondo database, testlab, con una tabella students. EXPLAIN mostra come viene eseguita una query: ogni riga letta prima dell'indice, dritto al risultato dopo." },
  { id: "dump", en: "mysqldump writes the database as SQL statements. Drop the table, read the file back, and the table is there again.",
    it: "mysqldump scrive il database come istruzioni SQL. Cancelliamo la tabella, rileggiamo il file, e la tabella torna." },
  { id: "phpmyadmin", en: "The same data in the browser: phpMyAdmin, forwarded to the host. Log in as labuser and open the users table.",
    it: "Gli stessi dati nel browser: phpMyAdmin, inoltrato all'host. Entra come labuser e apri la tabella users." },
  { id: "tests", en: "Six tests, one per exercise, each leaving the database as the lab hands it to you.",
    it: "Sei test, uno per esercizio, e ognuno lascia il database come il lab te lo consegna." },
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

// Rewritten 2026-10-07 in the approved style (docs/TOUR.md, "How a lesson types its commands"): the
// SQL typed in the mysql client, a clause per line, instead of squeezed into mysql -e "...".
const say = (d, cmd, read = 4000) => d.guest(cmd, { read });
// A line typed into the mysql client (no shell prompt to wait for).
async function sql(d, line, ms = 2500) {
  d.step(line);
  await d.type(" " + line, 40);
  await d.sleep(250);
  await d.key("Return");
  await d.sleep(ms);
}
const off = (d, cmd) => d.offCamera(VM, cmd);

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(3500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 1200 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.clearScreen();
  await d.session(VM);
  await say(d, "systemctl is-active mysql", 2000);
  await sql(d, "sudo mysql", 2500);
  await sql(d, "SHOW DATABASES;", 3500);
  await sql(d, "exit", 1000);
  await d.clearScreen();
  await sql(d, "mysql testdb", 2500);
  await sql(d, "SHOW TABLES;", 2500);
  await sql(d, "DESCRIBE users;", 4500);

  await d.cue("queries");
  await d.clearScreen();
  await sql(d, "SELECT * FROM users;", 3500);
  await sql(d, "SELECT name, email FROM users WHERE name LIKE 'A%';", 3500);
  await sql(d, "SELECT product, amount FROM orders ORDER BY amount DESC;", 4500);

  await d.cue("joins");
  await d.clearScreen();
  await sql(d, "SELECT u.name, o.product, o.amount", 400);
  await sql(d, "FROM users u", 400);
  await sql(d, "JOIN orders o ON o.user_id = u.id;", 5000);
  await d.clearScreen();
  await sql(d, "SELECT u.name, o.product", 400);
  await sql(d, "FROM users u", 400);
  await sql(d, "LEFT JOIN orders o ON o.user_id = u.id;", 5500);
  await d.clearScreen();
  await sql(d, "SELECT u.name, COUNT(o.id) AS orders, SUM(o.amount) AS total", 400);
  await sql(d, "FROM users u LEFT JOIN orders o ON o.user_id = u.id", 400);
  await sql(d, "GROUP BY u.id;", 5500);

  await d.cue("transactions");
  await d.clearScreen();
  await sql(d, "START TRANSACTION;", 1000);
  await sql(d, "INSERT INTO users (name) VALUES ('Ghost');", 1500);
  await sql(d, "SELECT COUNT(*) FROM users;", 3000);
  await sql(d, "ROLLBACK;", 1000);
  await sql(d, "SELECT COUNT(*) FROM users;", 3500);
  await sql(d, "DELETE FROM users WHERE name = 'Alice';", 5000);
  await sql(d, "exit", 1000);

  await d.cue("privileges");
  await d.clearScreen();
  await sql(d, "sudo mysql", 2500);
  await sql(d, "CREATE USER 'reader'@'localhost' IDENTIFIED BY 'readerpass';", 1500);
  await sql(d, "GRANT SELECT ON testdb.* TO 'reader'@'localhost';", 1500);
  await sql(d, "exit", 1000);
  await d.clearScreen();
  await sql(d, "mysql -u reader -preaderpass testdb", 2500);
  await sql(d, "SELECT COUNT(*) FROM users;", 3000);
  await sql(d, "INSERT INTO users (name) VALUES ('x');", 4500);
  await sql(d, "exit", 1000);
  off(d, `sudo mysql -e "DROP USER 'reader'@'localhost';"`);

  await d.cue("admin");
  off(d, `sudo mysql -e "CREATE DATABASE testlab; GRANT ALL ON testlab.* TO 'labuser'@'localhost';"; mysql testlab -e "CREATE TABLE students (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), grade INT); INSERT INTO students (name, grade) VALUES ('Ann', 28), ('Ben', 24), ('Cleo', 30);"`);
  await d.clearScreen();
  await sql(d, "mysql testlab", 2500);
  await sql(d, "SELECT * FROM students;", 3500);
  await sql(d, "EXPLAIN SELECT * FROM students WHERE grade = 30;", 5500);
  await sql(d, "CREATE INDEX idx_grade ON students (grade);", 1500);
  await sql(d, "EXPLAIN SELECT * FROM students WHERE grade = 30;", 6000);
  await sql(d, "exit", 1000);

  await d.cue("dump");
  await d.clearScreen();
  await say(d, "mysqldump --no-tablespaces testlab > testlab.sql", 1500);
  await say(d, "grep INSERT testlab.sql", 4500);
  await say(d, "mysql testlab -e 'DROP TABLE students;'", 1500);
  await say(d, "mysql testlab < testlab.sql", 1500);
  await say(d, "mysql testlab -e 'SELECT * FROM students;'", 5000);
  off(d, "sudo mysql -e 'DROP DATABASE testlab;'; rm -f ~/testlab.sql");
  await d.leave();

  await d.cue("phpmyadmin");
  await d.focusBrowser();
  const page = d.page;
  await page.bringToFront();
  await page.goto("http://127.0.0.1:8088/phpmyadmin/");
  await page.locator("#input_username").waitFor({ timeout: 30000 });
  await d.sleep(1500);
  await d.click(page.locator("#input_username"));
  await d.type("labuser");
  await d.click(page.locator("#input_password"));
  await d.type("labpass");
  await d.click(page.locator("#input_go"));
  await page.waitForLoadState("networkidle");
  await d.sleep(2500);
  await page.goto("http://127.0.0.1:8088/phpmyadmin/index.php?route=/sql&db=testdb&table=users&pos=0");
  await page.waitForLoadState("networkidle");
  await d.sleep(6000);
  await page.goto("about:blank");
  await d.focusTerminal();

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
