# Lab Docker: immagini, container, volumi e Compose

Un server Ubuntu 24.04 con Docker Engine e il plugin Compose dall'archivio di Ubuntu. L'utente
del lab è nel gruppo `docker`, quindi non serve `sudo`. Le due immagini che gli esercizi usano,
`alpine:3.20` e `nginx:1.27-alpine`, le scarica l'installazione: gli esercizi girano con
`--pull never` e non aspettano mai Docker Hub (né il suo limite di richieste). Portato dal
docker-lab di qlab.

| VM | Ruolo | SSH dall'host |
|---|---|---|
| `docker-lab-server` | Docker Engine 2 GB / 2 vCPU / disco 20 GB | `vmctl shell docker-lab-server` (127.0.0.1:2363) |

## Avvio

```bash
vmctl group install docker-lab    # un'immagine cloud, circa un minuto dopo il download, immagini già scaricate
vmctl shell docker-lab-server
```

## Esercizio 1: anatomia di Docker

Il motore è un demone, `dockerd`; il comando `docker` è un client che gli parla attraverso il
socket Unix `/var/run/docker.sock`. Far parte del gruppo `docker` dà accesso a quel socket — ed è
anche il motivo per cui vale quanto root su questa macchina.

```bash
systemctl status docker
docker version
docker info
id -nG
```

## Esercizio 2: immagini e container

Un'**immagine** è un modello in sola lettura fatto di layer; un **container** ne è un'istanza con
un suo sottile layer scrivibile.

```bash
docker image ls
docker image history alpine:3.20
docker run --rm --pull never alpine:3.20 cat /etc/os-release    # un colpo solo, rimosso quando finisce
docker run -d --name lab-web -p 8080:80 --pull never nginx:1.27-alpine
docker ps
curl -s localhost:8080 | grep '<title>'
```

`-d` lo manda in background, `-p 8080:80` pubblica la porta 80 del container sulla 8080 del server.

## Esercizio 3: dentro un container in esecuzione

```bash
docker exec lab-web nginx -v            # un secondo processo nel container
docker exec -it lab-web sh              # una shell dentro; exit per uscire
docker logs --tail 5 lab-web            # quello che ha stampato il processo principale
docker inspect -f '{{.State.Status}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' lab-web
docker rm -f lab-web                    # ferma e rimuovi
```

## Esercizio 4: volumi e bind mount

Quello che un container scrive nel suo layer sparisce con lui. Un **volume con nome** (gestito da
Docker sotto `/var/lib/docker/volumes`) o un **bind mount** (una directory dell'host) lo conserva.

```bash
docker volume create lab-data
docker run --rm --pull never -v lab-data:/data alpine:3.20 sh -c 'echo kept > /data/note'
docker run --rm --pull never -v lab-data:/data alpine:3.20 cat /data/note   # un altro container lo legge
mkdir -p ~/bind
docker run --rm --pull never -v ~/bind:/out alpine:3.20 sh -c 'date > /out/stamp'
cat ~/bind/stamp                        # il file è sul server
docker volume rm lab-data && rm -rf ~/bind
```

## Esercizio 5: un'app Compose

Compose descrive i container di un'applicazione, le porte e i volumi in un solo file.

```bash
mkdir -p ~/compose-demo/html && cd ~/compose-demo
echo '<h1>hello from compose</h1>' > html/index.html
cat > compose.yaml <<'EOF'
services:
  web:
    image: nginx:1.27-alpine
    pull_policy: never
    ports:
      - "8081:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
EOF
docker compose up -d
docker compose ps
curl -s localhost:8081
docker compose down
```

## Esercizio 6: costruire un'immagine da un Dockerfile

```bash
mkdir -p ~/build-demo && cd ~/build-demo
cat > Dockerfile <<'EOF'
FROM alpine:3.20
RUN echo built-in-the-lab > /message
CMD ["cat", "/message"]
EOF
docker build -t lab-hello .
docker run --rm lab-hello
docker image history lab-hello          # i tuoi layer sopra quelli di alpine
docker image rm lab-hello
```

## Test

```bash
vmctl group test docker-lab       # sei script; ognuno rimuove container, volumi, file e immagini che ha creato
```

## Spegnere e pulire

```bash
vmctl group down docker-lab
vmctl group clean docker-lab      # cancella il disco overlay (chiede prima); l'immagine cloud resta in isos/
```
