# MySQL lab: tables, queries, transactions, users, backups and phpMyAdmin

One server with MySQL 8 and a small shop database already loaded: `testdb` with `users` and `orders`,
a foreign key from each order to its user, and one user (Dana) who never ordered. phpMyAdmin shows the
same data in the browser. Ported from qlab's mysql-lab.

| VM | What | From the host |
|---|---|---|
| `mysql-lab-server` | MySQL 8, Apache + phpMyAdmin | SSH `vmctl shell mysql-lab-server` (127.0.0.1:2368), phpMyAdmin http://127.0.0.1:8088/phpmyadmin, MySQL 127.0.0.1:3307 |

The lab account is **labuser / labpass**, the same for everyone: a practice database, nothing to protect.
Inside the VM it is in `~/.my.cnf`, so `mysql testdb` logs in without typing it.

## Start

```sh
vmctl group install mysql-lab    # one cloud image, MySQL, phpMyAdmin and the sample data
vmctl shell mysql-lab-server
```

`bash ~/setup-mysql-lab.sh` is also the **reset**: it drops `testdb` (and what the exercises create) and
loads the sample data again.

## Exercise 1: MySQL anatomy

```sh
systemctl status mysql --no-pager | head -5
mysql --version
sudo mysql -e 'SHOW DATABASES;'          # root, over the socket
sudo ls /var/lib/mysql | head            # one directory per database
mysql testdb -e 'SHOW TABLES; DESCRIBE users;'
```

root has no password: it logs in through the Unix socket and MySQL checks that you are the system's
root (`auth_socket`). labuser has a password and could connect from another machine.

## Exercise 2: SQL queries

```sh
mysql testdb -e 'SELECT * FROM users;'
mysql testdb -e "SELECT name, email FROM users WHERE name LIKE 'A%';"
mysql testdb -e 'SELECT product, amount FROM orders ORDER BY amount DESC;'
mysql testdb -e 'SELECT u.name, o.product, o.amount FROM users u JOIN orders o ON o.user_id = u.id;'
mysql testdb -e 'SELECT u.name, o.product FROM users u LEFT JOIN orders o ON o.user_id = u.id;'
mysql testdb -e 'SELECT u.name, COUNT(o.id) AS orders, SUM(o.amount) AS total FROM users u LEFT JOIN orders o ON o.user_id = u.id GROUP BY u.id;'
```

`JOIN` keeps only the users with orders; `LEFT JOIN` keeps every user, with `NULL` where there is no
match: that is Dana. `GROUP BY` folds each user's orders into a count and a total.

## Exercise 3: changing data, in transactions

```sh
mysql testdb -e "INSERT INTO users (name, email) VALUES ('Eve', 'eve@example.com'); SELECT * FROM users;"
mysql testdb -e "UPDATE users SET email = 'eve@lab.local' WHERE name = 'Eve'; DELETE FROM users WHERE name = 'Eve';"
mysql testdb -e "START TRANSACTION; INSERT INTO users (name) VALUES ('Ghost'); SELECT COUNT(*) FROM users; ROLLBACK; SELECT COUNT(*) FROM users;"
mysql testdb -e "START TRANSACTION; INSERT INTO users (name) VALUES ('Frank'); COMMIT;"
mysql testdb -e "DELETE FROM users WHERE name = 'Alice';"    # refused: Alice has orders
```

Inside a transaction the change is visible to you but nothing is final until `COMMIT`; `ROLLBACK`
takes it back. The last command fails on purpose: the foreign key does not let an order lose its user.

## Exercise 4: users and privileges

```sh
sudo mysql -e "CREATE USER 'reader'@'localhost' IDENTIFIED BY 'Reader123!'; GRANT SELECT ON testdb.* TO 'reader'@'localhost';"
sudo mysql -e "SHOW GRANTS FOR 'reader'@'localhost';"
mysql -u reader -p'Reader123!' testdb -e 'SELECT COUNT(*) FROM users;'           # allowed
mysql -u reader -p'Reader123!' testdb -e "INSERT INTO users (name) VALUES ('x');" # denied
sudo mysql -e "REVOKE SELECT ON testdb.* FROM 'reader'@'localhost'; DROP USER 'reader'@'localhost';"
```

An account is a name **and** the host it connects from: `'reader'@'localhost'` and `'reader'@'%'` are
two different accounts. Give exactly what is needed.

## Exercise 5: administration, indexes, backup, restore

```sh
sudo mysql -e "CREATE DATABASE testlab; GRANT ALL ON testlab.* TO 'labuser'@'localhost';"
mysql testlab -e "CREATE TABLE students (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), grade INT); INSERT INTO students (name, grade) VALUES ('Ann', 28), ('Ben', 24), ('Cleo', 30);"
mysql testlab -e "ALTER TABLE students ADD COLUMN email VARCHAR(100); EXPLAIN SELECT * FROM students WHERE grade = 30;"
mysql testlab -e "CREATE INDEX idx_grade ON students (grade); EXPLAIN SELECT * FROM students WHERE grade = 30;"
mysqldump --no-tablespaces testlab > ~/testlab.sql && grep -c INSERT ~/testlab.sql
mysql testlab -e 'DROP TABLE students;' && mysql testlab < ~/testlab.sql && mysql testlab -e 'SELECT * FROM students;'
```

Compare the two `EXPLAIN`: `type: ALL` reads every row, `type: ref` with `key: idx_grade` goes
straight to the matching ones. The dump is plain SQL (`CREATE TABLE`, `INSERT`): reading it back
rebuilds the table. `--no-tablespaces` because labuser may dump data, not server-wide metadata.

## Exercise 6: security and configuration

```sh
ls /etc/mysql/mysql.conf.d/ && grep -E '^bind-address' /etc/mysql/mysql.conf.d/mysqld.cnf
ss -ltn | grep -E ':(3306|80) '
sudo mysql -e "SELECT user, host, plugin FROM mysql.user WHERE user IN ('root','labuser');"
sudo mysql -e 'SHOW PROCESSLIST;'
sudo mysql -e "SHOW VARIABLES LIKE 'log_error';"
```

`bind-address = 0.0.0.0` makes MySQL listen on every address: that is what lets the host reach it
through QEMU's NAT, where the guest sees the connection coming from 10.0.2.2. On a real server, listen
only where clients are, and firewall the rest. From the host:

```sh
xdg-open http://127.0.0.1:8088/phpmyadmin                                     # labuser / labpass
mysql -h 127.0.0.1 -P 3307 -u labuser -plabpass testdb -e 'SELECT * FROM orders;'    # with a mysql client on the host
```

When you are done, `bash ~/setup-mysql-lab.sh` puts `testdb` back as it was.

## Tests

```sh
vmctl group test mysql-lab
```

Six scripts, one per exercise, each leaving the database as the lab hands it to you.
