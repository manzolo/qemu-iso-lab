# Lab MySQL: tabelle, query, transazioni, utenti, backup e phpMyAdmin

Un server con MySQL 8 e un piccolo database di un negozio già caricato: `testdb` con `users` e `orders`,
una chiave esterna da ogni ordine al suo utente, e un utente (Dana) che non ha mai ordinato. phpMyAdmin
mostra gli stessi dati nel browser. Portato dal mysql-lab di qlab.

| VM | Cosa | Dall'host |
|---|---|---|
| `mysql-lab-server` | MySQL 8, Apache + phpMyAdmin | SSH `vmctl shell mysql-lab-server` (127.0.0.1:2368), phpMyAdmin http://127.0.0.1:8088/phpmyadmin, MySQL 127.0.0.1:3307 |

L'account del lab è **labuser / labpass**, uguale per tutti: un database di esercizio, niente da proteggere.
Dentro la VM sta in `~/.my.cnf`, così `mysql testdb` entra senza digitarlo.

## Inizio

```sh
vmctl group install mysql-lab    # una cloud image, MySQL, phpMyAdmin e i dati di esempio
vmctl shell mysql-lab-server
```

`bash ~/setup-mysql-lab.sh` è anche il **reset**: cancella `testdb` (e quello che creano gli esercizi) e
ricarica i dati di esempio.

## Esercizio 1: anatomia di MySQL

```sh
systemctl status mysql --no-pager | head -5
mysql --version
sudo mysql -e 'SHOW DATABASES;'          # root, attraverso il socket
sudo ls /var/lib/mysql | head            # una directory per database
mysql testdb -e 'SHOW TABLES; DESCRIBE users;'
```

root non ha password: entra dal socket Unix e MySQL controlla che tu sia il root del sistema
(`auth_socket`). labuser ha una password e potrebbe collegarsi da un'altra macchina.

## Esercizio 2: query SQL

```sh
mysql testdb -e 'SELECT * FROM users;'
mysql testdb -e "SELECT name, email FROM users WHERE name LIKE 'A%';"
mysql testdb -e 'SELECT product, amount FROM orders ORDER BY amount DESC;'
mysql testdb -e 'SELECT u.name, o.product, o.amount FROM users u JOIN orders o ON o.user_id = u.id;'
mysql testdb -e 'SELECT u.name, o.product FROM users u LEFT JOIN orders o ON o.user_id = u.id;'
mysql testdb -e 'SELECT u.name, COUNT(o.id) AS orders, SUM(o.amount) AS total FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id;'
```

`JOIN` tiene solo gli utenti con ordini; `LEFT JOIN` tiene tutti gli utenti, con `NULL` dove non c'è
corrispondenza: è Dana. `GROUP BY` riduce gli ordini di ogni utente a un conteggio e a un totale.

## Esercizio 3: modificare i dati, nelle transazioni

```sh
mysql testdb -e "INSERT INTO users (name, email) VALUES ('Eve', 'eve@example.com'); SELECT * FROM users;"
mysql testdb -e "UPDATE users SET email = 'eve@lab.local' WHERE name = 'Eve'; DELETE FROM users WHERE name = 'Eve';"
mysql testdb -e "START TRANSACTION; INSERT INTO users (name) VALUES ('Ghost'); SELECT COUNT(*) FROM users; ROLLBACK; SELECT COUNT(*) FROM users;"
mysql testdb -e "START TRANSACTION; INSERT INTO users (name) VALUES ('Frank'); COMMIT;"
mysql testdb -e "DELETE FROM users WHERE name = 'Alice';"    # rifiutato: Alice ha degli ordini
```

Dentro una transazione la modifica la vedi tu, ma niente è definitivo fino a `COMMIT`; `ROLLBACK` la
annulla. L'ultimo comando fallisce apposta: la chiave esterna non lascia un ordine senza il suo utente.

## Esercizio 4: utenti e privilegi

```sh
sudo mysql -e "CREATE USER 'reader'@'localhost' IDENTIFIED BY 'Reader123!'; GRANT SELECT ON testdb.* TO 'reader'@'localhost';"
sudo mysql -e "SHOW GRANTS FOR 'reader'@'localhost';"
mysql -u reader -p'Reader123!' testdb -e 'SELECT COUNT(*) FROM users;'           # permesso
mysql -u reader -p'Reader123!' testdb -e "INSERT INTO users (name) VALUES ('x');" # negato
sudo mysql -e "REVOKE SELECT ON testdb.* FROM 'reader'@'localhost'; DROP USER 'reader'@'localhost';"
```

Un account è un nome **e** l'host da cui si collega: `'reader'@'localhost'` e `'reader'@'%'` sono due
account diversi. Concedi esattamente quello che serve.

## Esercizio 5: amministrazione, indici, backup, ripristino

```sh
sudo mysql -e "CREATE DATABASE testlab; GRANT ALL ON testlab.* TO 'labuser'@'localhost';"
mysql testlab -e "CREATE TABLE students (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), grade INT); INSERT INTO students (name, grade) VALUES ('Ann', 28), ('Ben', 24), ('Cleo', 30);"
mysql testlab -e "ALTER TABLE students ADD COLUMN email VARCHAR(100); EXPLAIN SELECT * FROM students WHERE grade = 30;"
mysql testlab -e "CREATE INDEX idx_grade ON students (grade); EXPLAIN SELECT * FROM students WHERE grade = 30;"
mysqldump --no-tablespaces testlab > ~/testlab.sql && grep -c INSERT ~/testlab.sql
mysql testlab -e 'DROP TABLE students;' && mysql testlab < ~/testlab.sql && mysql testlab -e 'SELECT * FROM students;'
```

Confronta i due `EXPLAIN`: `type: ALL` legge ogni riga, `type: ref` con `key: idx_grade` va dritto a
quelle giuste. Il dump è SQL semplice (`CREATE TABLE`, `INSERT`): rileggerlo ricostruisce la tabella.
`--no-tablespaces` perché labuser può esportare i dati, non i metadati di tutto il server.

## Esercizio 6: sicurezza e configurazione

```sh
ls /etc/mysql/mysql.conf.d/ && grep -E '^bind-address' /etc/mysql/mysql.conf.d/mysqld.cnf
ss -ltn | grep -E ':(3306|80) '
sudo mysql -e "SELECT user, host, plugin FROM mysql.user WHERE user IN ('root','labuser');"
sudo mysql -e 'SHOW PROCESSLIST;'
sudo mysql -e "SHOW VARIABLES LIKE 'log_error';"
```

`bind-address = 0.0.0.0` fa ascoltare MySQL su ogni indirizzo: è ciò che permette all'host di
raggiungerlo attraverso il NAT di QEMU, dove il guest vede la connessione arrivare da 10.0.2.2. Su un
server vero si ascolta solo dove ci sono i client e si chiude il resto col firewall. Dall'host:

```sh
xdg-open http://127.0.0.1:8088/phpmyadmin                                     # labuser / labpass
mysql -h 127.0.0.1 -P 3307 -u labuser -plabpass testdb -e 'SELECT * FROM orders;'    # con un client mysql sull'host
```

Alla fine, `bash ~/setup-mysql-lab.sh` rimette `testdb` com'era.

## Test

```sh
vmctl group test mysql-lab
```

Sei script, uno per esercizio, e ognuno lascia il database come il lab te lo consegna.
