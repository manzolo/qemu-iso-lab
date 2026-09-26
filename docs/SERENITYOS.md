# Building the SerenityOS image

SerenityOS publishes no ISO and no disk image: the system is built from source, and the build
produces the disk the `serenityos` profile boots. This is the record of how the image in the
catalog was made on 2026-09-26, so that it can be done again (a newer commit, another builder)
without rediscovering the traps. `tools/build_serenityos.sh` is the same procedure as a script.

The result: `isos/serenityos-grub-cccf3076.img`, raw, MBR + GRUB, 1.39 GB, SHA-256
`55eabfe5978dc4cf4b0536c57fd4bbabccfa060f005762500387868c868042cb`, from commit
`cccf3076aacd936569f1136c6a4523b1a32a2597` of <https://github.com/SerenityOS/serenity>.

**Steps:** [1. A builder VM](#1-a-builder-vm) · [2. Toolchain and dependencies](#2-toolchain-and-dependencies) ·
[3. Clone and build](#3-clone-and-build) · [4. The disk image, and its three traps](#4-the-disk-image-and-its-three-traps) ·
[5. Back to the host](#5-back-to-the-host) · [6. Boot it](#6-boot-it) · [Doing it again](#doing-it-again)

## 1. A builder VM

Never on the host: the build installs a second GCC, compiles for one to three hours and the image
step needs root for loop devices. Any Ubuntu 22.04+ or Debian 12+ guest does; what it needs is
CPUs, RAM and disk:

| Resource | Used here | Minimum |
|---|---|---|
| vCPUs | 6 | 4 |
| RAM | 12 GB | 8 GB |
| Free disk | a dedicated 40 GB data disk on `~/lab` | 30 GB (toolchain ~4 GB, `Build/` ~15 GB, image 1.4 GB) |

The builder here was the `lubuntu22` libvirt VM (Lubuntu 22.04), reverted to its clean snapshot
and given the resources for the run only (`virsh setmaxmem`/`setmem` 12G, `setvcpus 6`, a second
qcow2 attached as `vdb` and mounted on `~/lab` through fstab). A lab profile works the same way,
with the sizes overridden in `local.json`:

```bash
vmctl bootstrap-unattended ubuntu-26.04     # or debian-server: any Linux with 4+ vCPUs, 8+ GB, 40 GB
vmctl shell ubuntu-26.04
```

Time on that VM (6 vCPUs of a nested KVM guest): about 2 hours for toolchain and system
together, then a few minutes for the image. A physical host with more cores is much faster.

## 2. Toolchain and dependencies

SerenityOS needs **GCC 14** on the host to build its own cross-compiler. Ubuntu 22.04 ships 11
and 12, so the `ubuntu-toolchain-r/test` PPA provides it; Ubuntu 24.04+ and Debian 13 have
`gcc-14` in their own archives (the script checks with `apt-cache show gcc-14` first).

```bash
sudo apt install software-properties-common git build-essential cmake curl libmpfr-dev libmpc-dev \
    libgmp-dev e2fsprogs ninja-build qemu-utils ccache rsync unzip texinfo libssl-dev zlib1g-dev \
    python3 grub-pc-bin grub2-common parted
sudo add-apt-repository -y ppa:ubuntu-toolchain-r/test      # Ubuntu 22.04 only
sudo apt install gcc-14 g++-14
```

`grub-pc-bin` and `parted` are for the image step, `qemu-utils` for `qemu-img`. CMake older
than 3.25 is not a problem: `Meta/serenity.sh` builds its own.

## 3. Clone and build

```bash
mkdir -p ~/lab && cd ~/lab
git clone https://github.com/SerenityOS/serenity.git && cd serenity
git checkout cccf3076aacd936569f1136c6a4523b1a32a2597
CC=gcc-14 CXX=g++-14 Meta/serenity.sh build x86_64 GNU
```

The commit is pinned on purpose: SerenityOS is rolling, `master` changes daily, and the profile
names the image after the commit (`serenityos-grub-<commit8>.img`) so that the disk in
`artifacts/` can always be traced to a source revision. `serenity.sh build` first builds the
cross toolchain into `Toolchain/Local/x86_64/`, then the whole system into `Build/x86_64/` with
ninja (this is where the time goes, and where a CPU-starved VM pays). Run it over SSH inside
`tmux` or `nohup`: a dropped session kills the build, and it resumes from where ninja stopped.

Do **not** use `Meta/serenity.sh run`, `image` or the `_disk_image` target: they build a
partition-less raw disk for SerenityOS's own QEMU script (kernel passed with `-kernel`), which
vmctl cannot boot. The bootable image is the GRUB one below.

## 4. The disk image, and its three traps

```bash
cd ~/lab/serenity/Build/x86_64
sudo env SERENITY_SOURCE_DIR=$HOME/lab/serenity SERENITY_ARCH=x86_64 SERENITY_TOOLCHAIN=GNU \
    SUDO_UID=$(id -u) SUDO_GID=$(id -g) ../../Meta/build-image-grub.sh mbr
```

`build-image-grub.sh mbr` creates `grub_disk_image`: one ext2 partition in an MBR table,
the system rsynced into it and GRUB installed with `grub-install --target=i386-pc` on a loop
device. On Ubuntu 22.04 it failed three times before producing an image, always the same way
(`rsync: write failed ... No space left on device (28)` on the same file), and each time for a
different reason. The script in `tools/` patches the first two into its working copy and
cleans the third; by hand they are:

1. **The image is too small.** The script sizes it as base + root + 300 MB; ext2 with this
   many small files needs more. Change `+ 300))` to `+ 600))` in `Meta/build-image-grub.sh`
   (`sed -i 's/+ 300))/+ 600))/'`).
2. **The partition keeps its old size.** After `parted` rewrote the table, `/dev/loopNp1` still
   reported the size of the previous run until the kernel re-read it, so `mke2fs` formatted a
   smaller partition than the one on disk. Add `partx -u "${dev}"` (or `partprobe`) and a
   `sleep 1` before the `destroying old filesystem` line.
3. **A regular file where the partition node should be.** The one that took longest to see:
   an early write to `/dev/loop11p1`, before udev had created the node, left a 1 MB
   *regular file* at that path. Every later run then formatted and filled that file, whatever
   the image size, and `ls -l /dev/loop11p1` showed `-rw-r--r--` instead of `brw-rw----`.
   Remove it: `for f in /dev/loop*p*; do [ -f "$f" ] && [ ! -b "$f" ] && sudo rm -f "$f"; done`.

After that the run ends with `grub-install` reporting no errors and a `grub_disk_image` of
about 1.4 GB. Name it after the commit and keep the hash:

```bash
mv grub_disk_image ~/lab/serenityos-grub-cccf3076.img
sha256sum ~/lab/serenityos-grub-cccf3076.img
```

## 5. Back to the host

The image went through the VM's shared folder (virtiofs, `storage/shared/` on this host); `scp`
from the host to the builder works the same: 

```bash
scp -P 2275 -i artifacts/ubuntu-26.04/ssh/id_ed25519 lab@127.0.0.1:lab/serenityos-grub-cccf3076.img isos/
sha256sum isos/serenityos-grub-cccf3076.img       # must match the hash printed in the VM
```

The profile expects it at `disk_image.path` (`isos/serenityos-grub-cccf3076.img`, format
`raw`). `isos/` is gitignored, and the image is a local artifact like a downloaded ISO. Then the
builder can be stopped, cleaned, or reverted to its snapshot.

## 6. Boot it

```bash
vmctl prep serenityos        # qemu-img convert of the image into artifacts/serenityos/disk.qcow2
vmctl start serenityos       # or Boot with display / Boot headless from the web page
```

`prep` records origin `image` in the VM's `state.json`; `install` and `provision` refuse a
`disk_image` profile because there is nothing to install. The machine is q35, BIOS, the disk
on SATA, an `e1000` NIC and standard VGA: SerenityOS reaches its desktop with a Terminal open in
about 15 s, logged in as `anon` (password `foo`; `su` gives root without a password — the
*Login* row of the dashboard shows this). No SSH and no guest agent: the console and the
screenshot are the way in.

## Doing it again

For a newer SerenityOS, the whole procedure is the script:

```bash
scp tools/build_serenityos.sh <builder>:
ssh <builder> sudo sh build_serenityos.sh ~/serenity-build <commit>
```

It installs the packages, clones the pinned commit, builds, applies the two patches, removes a
stray loop file and runs `build-image-grub.sh mbr`, ending with the image's SHA-256 and path.
Then copy the image to `isos/`, set `disk_image.path` and `disk_image.commit` in the profile (or
in `local.json` for a private build), `vmctl clean serenityos && vmctl prep serenityos`, boot,
and update `meta.verified` only after it reached the desktop.
