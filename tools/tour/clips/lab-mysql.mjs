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
  { id: "admin", en: "EXPLAIN shows how a query runs: every row read before the index, straight to the match after it. mysqldump writes the database as SQL, and reading it back restores it.",
    it: "EXPLAIN mostra come viene eseguita una query: ogni riga letta prima dell'indice, dritto al risultato dopo. mysqldump scrive il database come SQL, e rileggerlo lo ripristina." },
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

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
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
  await d.run("clear");
  await d.session(VM);
  await d.guest("systemctl is-active mysql; mysql --version", { read: 3000 });
  await d.guest("sudo mysql -e 'SHOW DATABASES;'    # root, over the socket", { read: 3500 });
  await d.guest("mysql testdb -e 'SHOW TABLES; DESCRIBE users;'    # labuser, from ~/.my.cnf", { read: 5000 });

  await d.cue("queries");
  await d.guest("clear; mysql testdb -e 'SELECT * FROM users;'", { read: 3500 });
  await d.guest("mysql testdb -e \"SELECT name, email FROM users WHERE name LIKE 'A%';\"", { read: 3000 });
  await d.guest("mysql testdb -e 'SELECT product, amount FROM orders ORDER BY amount DESC;'", { read: 3500 });

  await d.cue("joins");
  await d.guest("clear; mysql testdb -e 'SELECT u.name, o.product, o.amount FROM users u JOIN orders o ON o.user_id = u.id;'", { read: 4000 });
  await d.guest("mysql testdb -e 'SELECT u.name, o.product FROM users u LEFT JOIN orders o ON o.user_id = u.id;'    # Dana: NULL", { read: 4500 });
  await d.guest("mysql testdb -e 'SELECT u.name, COUNT(o.id) AS orders, SUM(o.amount) AS total FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id;'", { read: 5000 });

  await d.cue("transactions");
  await d.guest("clear; mysql testdb -e \"START TRANSACTION; INSERT INTO users (name) VALUES ('Ghost'); SELECT COUNT(*) FROM users; ROLLBACK; SELECT COUNT(*) FROM users;\"", { read: 5000 });
  await d.guest("mysql testdb -e \"DELETE FROM users WHERE name = 'Alice';\"    # refused: Alice has orders", { read: 4500 });

  await d.cue("privileges");
  await d.guest("clear; sudo mysql -e \"CREATE USER 'reader'@'localhost' IDENTIFIED BY 'Reader123!'; GRANT SELECT ON testdb.* TO 'reader'@'localhost'; SHOW GRANTS FOR 'reader'@'localhost';\"", { read: 4000 });
  await d.guest("mysql -u reader -p'Reader123!' testdb -e 'SELECT COUNT(*) FROM users;'    # allowed", { read: 3000 });
  await d.guest("mysql -u reader -p'Reader123!' testdb -e \"INSERT INTO users (name) VALUES ('x');\"    # denied", { read: 3500 });
  await d.guest("sudo mysql -e \"DROP USER 'reader'@'localhost';\"", { read: 1500 });

  await d.cue("admin");
  await d.guest("clear; sudo mysql -e \"CREATE DATABASE testlab; GRANT ALL ON testlab.* TO 'labuser'@'localhost';\" && mysql testlab -e \"CREATE TABLE students (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), grade INT); INSERT INTO students (name, grade) VALUES ('Ann', 28), ('Ben', 24), ('Cleo', 30);\"", { read: 2000 });
  await d.guest("mysql testlab -e 'EXPLAIN SELECT * FROM students WHERE grade = 30\\G' | grep -E 'type|key:|rows'    # before the index", { read: 4000 });
  await d.guest("mysql testlab -e 'CREATE INDEX idx_grade ON students (grade); EXPLAIN SELECT * FROM students WHERE grade = 30\\G' | grep -E 'type|key:|rows'    # after", { read: 4500 });
  await d.guest("mysqldump --no-tablespaces testlab > ~/testlab.sql && grep -c INSERT ~/testlab.sql && mysql testlab -e 'DROP TABLE students;' && mysql testlab < ~/testlab.sql && mysql testlab -e 'SELECT * FROM students;'", { read: 5000 });
  await d.guest("sudo mysql -e 'DROP DATABASE testlab;'; rm ~/testlab.sql", { read: 1000 });
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
  await d.run("clear");
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
