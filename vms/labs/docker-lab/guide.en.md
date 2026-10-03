# Docker lab: images, containers, volumes and Compose

One Ubuntu 24.04 server with Docker Engine and the Compose plugin from Ubuntu's archive. The lab
user is in the `docker` group, so no `sudo` is needed. The two images the exercises use,
`alpine:3.20` and `nginx:1.27-alpine`, are pulled by the install: the exercises run with
`--pull never` and never wait on Docker Hub (or its rate limit). Ported from qlab's docker-lab.

| VM | Role | SSH from the host |
|---|---|---|
| `docker-lab-server` | Docker Engine 2 GB / 2 vCPUs / 20 GB disk | `vmctl shell docker-lab-server` (127.0.0.1:2363) |

## Start

```bash
vmctl group install docker-lab    # one cloud image, about a minute after the download, images pre-pulled
vmctl shell docker-lab-server
```

## Exercise 1: Docker anatomy

The engine is a daemon, `dockerd`; the `docker` command is a client that talks to it over the
Unix socket `/var/run/docker.sock`. Belonging to the `docker` group is what grants access to that
socket — which is also why it is as powerful as root on this machine.

```bash
systemctl status docker
docker version
docker info
id -nG
```

## Exercise 2: images and containers

An **image** is a read-only template made of layers; a **container** is an instance of it with a
thin writable layer of its own.

```bash
docker image ls
docker image history alpine:3.20
docker run --rm --pull never alpine:3.20 cat /etc/os-release    # one shot, removed when it ends
docker run -d --name lab-web -p 8080:80 --pull never nginx:1.27-alpine
docker ps
curl -s localhost:8080 | grep '<title>'
```

`-d` runs it in the background, `-p 8080:80` publishes the container's port 80 on the server's 8080.

## Exercise 3: inside a running container

```bash
docker exec lab-web nginx -v            # a second process in the running container
docker exec -it lab-web sh              # a shell inside; exit to leave
docker logs --tail 5 lab-web            # what the main process printed
docker inspect -f '{{.State.Status}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' lab-web
docker rm -f lab-web                    # stop and remove
```

## Exercise 4: volumes and bind mounts

What a container writes in its own layer disappears with it. A **named volume** (managed by
Docker under `/var/lib/docker/volumes`) or a **bind mount** (a directory of the host) keeps it.

```bash
docker volume create lab-data
docker run --rm --pull never -v lab-data:/data alpine:3.20 sh -c 'echo kept > /data/note'
docker run --rm --pull never -v lab-data:/data alpine:3.20 cat /data/note   # another container reads it
mkdir -p ~/bind
docker run --rm --pull never -v ~/bind:/out alpine:3.20 sh -c 'date > /out/stamp'
cat ~/bind/stamp                        # the file is on the server
docker volume rm lab-data && rm -rf ~/bind
```

## Exercise 5: a Compose app

Compose describes the containers of an application, their ports and volumes in one file.

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

## Exercise 6: build an image from a Dockerfile

```bash
mkdir -p ~/build-demo && cd ~/build-demo
cat > Dockerfile <<'EOF'
FROM alpine:3.20
RUN echo built-in-the-lab > /message
CMD ["cat", "/message"]
EOF
docker build -t lab-hello .
docker run --rm lab-hello
docker image history lab-hello          # your layers on top of alpine's
docker image rm lab-hello
```

## Tests

```bash
vmctl group test docker-lab       # six scripts; each removes the containers, volumes, files and images it made
```

## Stop and clean

```bash
vmctl group down docker-lab
vmctl group clean docker-lab      # deletes the overlay disk (asks first); the cloud image stays in isos/
```
