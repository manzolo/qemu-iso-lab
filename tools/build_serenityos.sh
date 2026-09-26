#!/bin/sh
# Build the SerenityOS disk image that the `serenityos` profile boots (SerenityOS publishes no
# ISO or image: it is built from source). Run it as root INSIDE a disposable Ubuntu/Debian
# builder VM, never on the host: it installs a toolchain, compiles for 1-3 hours and needs loop
# devices for the image. Verified on a Lubuntu 22.04 VM with 6 vCPUs / 12 GB on 2026-09-26.
#
#   sudo sh build_serenityos.sh [/work/dir] [commit]
#
# Output: <workdir>/serenityos-grub-<commit8>.img (raw, MBR + GRUB, ~1.4 GB). Copy it to the
# host as isos/serenityos-grub-<commit8>.img, then: vmctl prep serenityos && vmctl start serenityos.
set -eu
WORK=${1:-$HOME/serenity-build}
COMMIT=${2:-cccf3076aacd936569f1136c6a4523b1a32a2597}
USER_NAME=${SUDO_USER:-$(id -un)}
[ "$(id -u)" = 0 ] || { echo "run as root (the image step uses loop devices)" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get update -q
apt-get install -y -q software-properties-common git build-essential cmake curl libmpfr-dev libmpc-dev \
    libgmp-dev e2fsprogs ninja-build qemu-utils ccache rsync unzip texinfo libssl-dev zlib1g-dev \
    python3 grub-pc-bin grub2-common parted
# SerenityOS needs GCC 14; Ubuntu 22.04 ships 11/12, the toolchain PPA has 14 for jammy.
if ! apt-cache show gcc-14 >/dev/null 2>&1; then
    add-apt-repository -y ppa:ubuntu-toolchain-r/test
    apt-get update -q
fi
apt-get install -y -q gcc-14 g++-14

mkdir -p "$WORK" && chown "$USER_NAME" "$WORK"
if [ ! -d "$WORK/serenity/.git" ]; then
    su "$USER_NAME" -c "git clone -q https://github.com/SerenityOS/serenity.git '$WORK/serenity'"
fi
su "$USER_NAME" -c "cd '$WORK/serenity' && git fetch -q --depth 1 origin $COMMIT && git checkout -q $COMMIT"

# Toolchain + system, as the user (CMake older than 3.25 is built by the script itself).
su "$USER_NAME" -c "cd '$WORK/serenity' && CC=gcc-14 CXX=g++-14 Meta/serenity.sh build x86_64 GNU"

cd "$WORK/serenity"
# Two fixes to Meta/build-image-grub.sh, verified on Ubuntu 22.04 (working copy only):
# - the size it computes (Base + Root + 300 MB) is too small for ext2 with this many small files;
# - after parted, the loop partition keeps its stale size until the table is re-read.
sed -i 's/+ 300))/+ 600))/' Meta/build-image-grub.sh
grep -q 'partx -u' Meta/build-image-grub.sh || sed -i 's/^printf "destroying old filesystem/partx -u "${dev}" 2>\/dev\/null || partprobe "${dev}" 2>\/dev\/null || true\nsleep 1\n&/' Meta/build-image-grub.sh
# A write to /dev/loopNp1 before udev created the node leaves a *regular file* there, and every
# later mke2fs formats that 1 MB file instead of the partition ("No space left" in rsync).
for f in /dev/loop*p*; do [ -f "$f" ] && [ ! -b "$f" ] && rm -f "$f"; done

cd Build/x86_64
rm -f grub_disk_image
SERENITY_SOURCE_DIR="$WORK/serenity" SERENITY_ARCH=x86_64 SERENITY_TOOLCHAIN=GNU \
    SUDO_UID=$(id -u "$USER_NAME") SUDO_GID=$(id -g "$USER_NAME") \
    "$WORK/serenity/Meta/build-image-grub.sh" mbr
OUT="$WORK/serenityos-grub-$(echo "$COMMIT" | cut -c1-8).img"
mv grub_disk_image "$OUT"
chown "$USER_NAME" "$OUT"
sha256sum "$OUT"
echo "image: $OUT"
