#!/usr/bin/env bash
# mysql-lab: MySQL 8 with the lab's database, the lab account and phpMyAdmin. Idempotent, and the
# lab's reset: every run drops testdb (and the exercises' extra database and user) and loads the
# sample data again. Runs as the lab user with sudo (post_install_run, or by hand: bash ~/setup-mysql-lab.sh).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# phpMyAdmin asks for its web server and its database during the install: answered beforehand.
if ! dpkg -s phpmyadmin >/dev/null 2>&1; then
    sudo debconf-set-selections <<'SEL'
phpmyadmin phpmyadmin/dbconfig-install boolean true
phpmyadmin phpmyadmin/mysql/admin-pass password
phpmyadmin phpmyadmin/mysql/app-pass password labpass
phpmyadmin phpmyadmin/app-password-confirm password labpass
phpmyadmin phpmyadmin/reconfigure-webserver multiselect apache2
SEL
    sudo apt-get install -y -q phpmyadmin >/dev/null
fi
sudo phpenmod mbstring
sudo ln -sfn /usr/share/phpmyadmin /var/www/html/phpmyadmin

# Listen on every address: the host reaches 3306 through QEMU's NAT (127.0.0.1:3307), which the
# guest sees as a connection from 10.0.2.2, not from localhost.
sudo sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mysql.conf.d/mysqld.cnf
sudo systemctl restart mysql apache2

sudo mysql <<'SQL'
DROP DATABASE IF EXISTS testdb;
DROP DATABASE IF EXISTS testlab;
DROP USER IF EXISTS 'reader'@'localhost';
CREATE DATABASE testdb;
CREATE USER IF NOT EXISTS 'labuser'@'localhost' IDENTIFIED BY 'labpass';
CREATE USER IF NOT EXISTS 'labuser'@'%' IDENTIFIED BY 'labpass';
GRANT ALL PRIVILEGES ON testdb.* TO 'labuser'@'localhost';
GRANT ALL PRIVILEGES ON testdb.* TO 'labuser'@'%';
FLUSH PRIVILEGES;
USE testdb;
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO users (name, email) VALUES
    ('Alice', 'alice@example.com'),
    ('Bob', 'bob@example.com'),
    ('Charlie', 'charlie@example.com'),
    ('Dana', 'dana@example.com');
CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    product VARCHAR(100) NOT NULL,
    amount DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
INSERT INTO orders (user_id, product, amount) VALUES
    (1, 'Laptop', 999.99),
    (2, 'Mouse', 29.99),
    (1, 'Keyboard', 79.99),
    (3, 'Monitor', 349.99);
SQL
# ~/.my.cnf: `mysql testdb` logs in as labuser without typing the password (the lab's password,
# the same for everyone: this is a practice database).
printf '[client]\nuser=labuser\npassword=labpass\n' > ~/.my.cnf
chmod 600 ~/.my.cnf
echo "mysql-lab ready: testdb with $(sudo mysql -N -e 'SELECT COUNT(*) FROM testdb.users') users and $(sudo mysql -N -e 'SELECT COUNT(*) FROM testdb.orders') orders"
