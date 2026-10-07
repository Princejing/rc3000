#!/bin/sh
# RC3000 全分区备份。在路由器上执行（需要 root shell）。
# 会把所有 mtd 分区 dump 到 /tmp/rc3000-backup/，并打包成一个 tar。
#
# 用法:
#   sh backup.sh                 # 只备份到 /tmp
#   sh backup.sh 192.168.1.2     # 备份并通过 tftp 上传到 PC（PC 需开 tftp 服务器）
#
# 备份完务必把 tar 拉到电脑上长期保存 —— 这是唯一的救砖保险。

set -u
DEST=/tmp/rc3000-backup
PC_IP="${1:-}"
LOG="$DEST/backup.log"

mkdir -p "$DEST"
: > "$LOG"

log() { echo "$@" | tee -a "$LOG"; }

log "=== RC3000 分区备份 $(date) ==="
cat /proc/mtd > "$DEST/proc_mtd.txt" 2>/dev/null
fw_printenv  > "$DEST/uboot_env.txt" 2>/dev/null
dmesg        > "$DEST/dmesg.txt"     2>/dev/null

# ART 是射频校准数据，必须优先且完整备份
log ""
log ">>> 优先级最高: ART 分区"
ART_DEV=$(grep -iE '"0:ART"|"ART"' /proc/mtd 2>/dev/null | head -1 | cut -d: -f1)
if [ -n "$ART_DEV" ]; then
    dd if=/dev/$ART_DEV of="$DEST/art.bin" bs=64k 2>/dev/null
    log "  art.bin  $(wc -c < "$DEST/art.bin") bytes  (来自 $ART_DEV)"
    md5sum "$DEST/art.bin" >> "$LOG"
else
    log "  !! 没找到 ART 分区，请手工确认 /proc/mtd"
fi

log ""
log ">>> 全部分区"
for line in $(grep '^mtd' /proc/mtd 2>/dev/null | cut -d: -f1); do
    size=$(grep "^$line:" /proc/mtd | awk '{print $2}')
    name=$(grep "^$line:" /proc/mtd | awk '{print $4}' | tr -d '"' | tr '/' '_')
    [ -z "$name" ] && name="$line"
    out="$DEST/${line}_${name}.bin"
    dd if=/dev/$line of="$out" bs=64k 2>/dev/null
    sz=$(wc -c < "$out" 2>/dev/null || echo 0)
    log "  $line $name -> ${sz} bytes"
    if [ "$sz" -eq 0 ]; then rm -f "$out"; fi
done

# 已知的大分区单独再存一份有意义的命名
ROOTFS_MTD=$(cat /sys/class/ubi/ubi0/mtd_num 2>/dev/null)
if [ -n "$ROOTFS_MTD" ]; then
    dd if=/dev/mtd${ROOTFS_MTD} of="$DEST/rootfs_current.bin" bs=128k 2>/dev/null
    log ""
    log "当前启动的 rootfs: mtd${ROOTFS_MTD} -> rootfs_current.bin ($(wc -c < "$DEST/rootfs_current.bin") bytes)"
fi

log ""
tar cf "$DEST.tar" -C /tmp rc3000-backup 2>/dev/null
SIZE=$(wc -c < "$DEST.tar" 2>/dev/null || echo 0)
log "打包完成: $DEST.tar ($SIZE bytes)"

if [ -n "$PC_IP" ]; then
    log ""
    log "上传到 tftp://$PC_IP ..."
    tftp -p -l "$DEST.tar" -r rc3000-backup.tar "$PC_IP" 2>&1 | tee -a "$LOG" \
        || log "tftp 上传失败，请检查 PC 上的 tftp 服务器"
fi

log ""
log "=== 完成 ==="
log "重要: 把 $DEST.tar 复制到电脑长期保存。art.bin 丢了无线就废了。"
