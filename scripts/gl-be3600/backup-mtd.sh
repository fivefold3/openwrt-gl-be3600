#!/bin/bash
# Full NAND backup of a GL-BE3600 over SSH, taken from the PC.
# Works on stock firmware (with the stock root password) or on OpenWrt.
#
# Usage: bash scripts/gl-be3600/backup-mtd.sh [root@<router address>]
#        (default root@192.168.1.1; stock firmware uses 192.168.8.1)
# Opens three ssh sessions, so expect three password prompts.
# Writes ./backup/mtd<N>_<name>.bin and SHA256SUMS.
#
# mtd11 (0:ART) holds the router's radio calibration and MAC addresses and
# exists nowhere else. Keep the backup somewhere safe.
set -e
R="${1:-root@192.168.1.1}"
mkdir -p backup
cd backup

echo "== [1/3] boot-critical partitions mtd0..mtd14 (~18 MB, staged in router RAM)"
ssh "$R" 'rm -rf /tmp/mtdbak && mkdir /tmp/mtdbak && cd /tmp/mtdbak &&
  for i in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
    n=$(grep "^mtd$i:" /proc/mtd | cut -d\" -f2 | tr ":" "_");
    cat /dev/mtd$i > mtd${i}_${n}.bin;
  done && tar -cf - *.bin && rm -rf /tmp/mtdbak' > mtd0-14.tar
tar -xf mtd0-14.tar && rm mtd0-14.tar

echo "== [2/3] rootfs partition mtd15 (492 MB, streamed; takes a few minutes)"
ssh "$R" 'cat /dev/mtd15' > mtd15_rootfs.bin

echo "== [3/3] cleanup check + checksums"
ssh "$R" 'ls /tmp/mtdbak 2>/dev/null || echo "router /tmp clean"'
sha256sum *.bin | tee SHA256SUMS

echo
echo "== sizes (expected: mtd11 ART = 2097152 bytes)"
ls -la *.bin
echo "DONE. Backup is in ./backup/ - copy it somewhere safe as well."
