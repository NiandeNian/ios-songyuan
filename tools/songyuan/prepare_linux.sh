#!/usr/bin/env bash
# Prepare local Ubuntu/WSL to read APFS and create the local HFSX iOS root.
set -euo pipefail
workspace=$(cd "$(dirname "$0")/../.." && pwd)
work=/opt/unixnu-songyuan
mkdir -p "$work"
mkdir -p "$workspace/.local/linux"
fetch_source() {
  local repo=$1 revision=$2 dest=$3
  if [ ! -f "$dest/.source-revision" ]; then
    mkdir -p "$dest"
    curl -fsSL --connect-timeout 20 --max-time 300 --retry 3 \
      "https://codeload.github.com/$repo/tar.gz/$revision" -o "$dest/source.tar.gz"
    tar -xzf "$dest/source.tar.gz" --strip-components=1 -C "$dest"
    printf '%s\n' "$revision" > "$dest/.source-revision"
  fi
}
sudo apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  build-essential gcc-11 bc bison flex libelf-dev libssl-dev libncurses-dev \
  dwarves cpio cmake git rsync hfsprogs libfuse3-dev fuse3 zlib1g-dev libbz2-dev \
  libattr1-dev unzip pkg-config

if ! command -v apfs-fuse >/dev/null; then
  fetch_source sgan81/apfs-fuse 66b86bd525e8cb90f9012543be89b1f092b75cf3 "$work/apfs-fuse"
  fetch_source lzfse/lzfse e634ca58b4821d9f3d560cdc6df5dec02ffc93fd "$work/apfs-fuse/3rdparty/lzfse"
  cmake -S "$work/apfs-fuse" -B "$work/apfs-fuse/build" -DUSE_FUSE3=ON
  cmake --build "$work/apfs-fuse/build" -j4
  sudo install -m755 "$work/apfs-fuse/build/apfs-fuse" /usr/local/bin/apfs-fuse
fi

if ! grep -qw hfsplus /proc/filesystems; then
  version=$(uname -r)
  case "$version" in
    *-microsoft-standard-WSL2) ;;
    *) echo 'This helper requires the Microsoft WSL2 kernel.' >&2; exit 1 ;;
  esac
  tag="linux-msft-wsl-${version%%-microsoft*}"
  kernel="$work/wsl-kernel"
  if [ "$tag" != linux-msft-wsl-5.10.16.3 ]; then
    echo "This revision is pinned for WSL 5.10.16.3; update the source pin for $tag" >&2; exit 1
  fi
  fetch_source microsoft/WSL2-Linux-Kernel a571dc8cedc8e0e56487c0dc93243e0b5db8960a "$kernel"
  (
    cd "$kernel"
    if [ ! -f .config ]; then
      zcat /proc/config.gz > .config
      scripts/config --module HFSPLUS_FS --disable LOCALVERSION_AUTO
      scripts/config --disable DEBUG_INFO_BTF
      make CC=gcc-11 olddefconfig
    fi
    # MODVERSIONS requires a complete build to get valid symbol CRCs.
    make -j8 CC=gcc-11 LOCALVERSION= > "$workspace/.local/linux/kernel-build.log" 2>&1
    sudo insmod fs/hfsplus/hfsplus.ko
  )
fi
grep -w hfsplus /proc/filesystems
echo 'APFS/HFSX preparation complete'
