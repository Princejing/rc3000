#!/bin/sh
# 在 RC3000 上执行（Telnet 登录后输入 debugshell 拿到 root 再跑）。
# 采集硬件真实信息，输出 /tmp/hwinfo.json，供 PC 端 hwinfo2dts.py 生成板级 DTS。
#
#   上传:  把本机当 tftp 服务器，在路由器上执行
#          tftp -p -l /tmp/hwinfo.json -r hwinfo.json <你的电脑IP>
#   或者:  http 方式  curl -F f=@/tmp/hwinfo.json http://<电脑IP>:8000/

set -u
OUT=/tmp/hwinfo.json

j() { printf '"%s":"%s",' "$1" "$2"; }

echo "== 机型与固件版本 =="
cat /proc/mtd 2>/dev/null | sed -n '1,40p'

echo
echo "== CPU =="
sed -n 's/^Hardware[[:space:]]*:[[:space:]]*//p;s/^machine[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo 2>/dev/null | head -2
grep -m1 -i 'ipq50' /proc/cpuinfo 2>/dev/null

echo "== 内存 =="
awk '/MemTotal/{print $2" kB"}' /proc/meminfo 2>/dev/null

echo "== 无线芯片 =="
ls /sys/class/ieee80211/ 2>/dev/null
for p in /sys/class/ieee80211/phy*; do
  [ -e "$p" ] || continue
  echo "  $(basename $p): $(cat $p/device/modalias 2>/dev/null)"
done

echo "== 交换机 / 网卡 =="
swconfig list 2>/dev/null
ls /sys/class/net/ 2>/dev/null
cat /proc/switch/*/model 2>/dev/null

echo "== U-Boot 环境变量 =="
fw_printenv 2>/dev/null | grep -iE 'productname|productconfig|mtdparts|bootcmd|baudrate|ethaddr' 

echo
echo "== 生成 $OUT =="

# --- 组装 JSON ---
CPU=$(grep -m1 -ioE 'IPQ[0-9]{4}' /proc/cpuinfo 2>/dev/null | head -1 | tr 'a-z' 'A-Z')
[ -z "$CPU" ] && CPU=$(sed -n 's/^Hardware[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo 2>/dev/null | grep -o -iE 'IPQ[0-9]+' | head -1 | tr 'a-z' 'A-Z')
[ -z "$CPU" ] && CPU="unknown"

MEM_MB=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1024 ))

# 交换机判别: 优先看 swconfig，其次看内核启动日志里的驱动名
SWITCH="unknown"
if swconfig list 2>/dev/null | grep -qi 'rtl8367\|rtl8365'; then SWITCH="rtl8367s"; fi
if swconfig list 2>/dev/null | grep -qi 'qca8337\|qca8k';    then SWITCH="qca8337";   fi
if [ "$SWITCH" = "unknown" ]; then
  dmesg 2>/dev/null | grep -qiE 'rtl8365mb|rtl8367' && SWITCH="rtl8367s"
  dmesg 2>/dev/null | grep -qiE 'qca8k|qca8337'     && SWITCH="qca8337"
fi

# 闪存总量: 累加 /proc/mtd 里 0: 前缀分区的 size
# 不用 awk 的 strtonum（busybox awk 没有），改用纯 shell 做 16 进制转换
NAND_MB=0
TOTAL=0
while read -r dev size es name; do
  case "$dev" in mtd*) ;; *) continue ;; esac
  case "$name" in
    *rootfs*|*kernel*|*ubi*|*wifi_fw*|*bt_fw*|*pdt_data*|*plugin*|*exp*) continue ;;
  esac
  [ -n "$size" ] && TOTAL=$(( TOTAL + 0x$size ))
done < /proc/mtd
NAND_MB=$(( TOTAL / 1048576 ))

ROOTFS_MTD=$(cat /sys/class/ubi/ubi0/mtd_num 2>/dev/null || echo "")
KERNEL_DEV=$(grep -iE '"kernel"' /proc/mtd 2>/dev/null | cut -d: -f1)
PART_TOTAL=$(grep -c '^mtd' /proc/mtd 2>/dev/null)

{
  printf '{'
  j model  "$(fw_printenv -n productname 2>/dev/null || echo RC3000)"
  j soc    "$CPU"
  j mem_mb "$MEM_MB"
  j nand_mb "$NAND_MB"
  j switch "$SWITCH"
  j rootfs_mtd "$ROOTFS_MTD"
  j kernel_mtd "$KERNEL_DEV"
  j mtd_count "$PART_TOTAL"
  j uboot_baud "$(fw_printenv -n baudrate 2>/dev/null || echo 115200)"
  j fw_version "$(cat /etc/version 2>/dev/null | head -1 || echo unknown)"
  printf '"mtd_raw":['
  first=1
  while read -r dev size es name; do
    case "$dev" in mtd*) ;; *) continue ;; esac
    [ $first -eq 1 ] || printf ','
    first=0
    printf '{"dev":"%s","size":"%s","name":"%s"}' "$dev" "$size" "$(echo $name | tr -d '"')"
  done < /proc/mtd
  printf ']}'
} > "$OUT" 2>/dev/null

echo
cat "$OUT"
echo
echo "---- 请把 /tmp/hwinfo.json 传回电脑，然后执行 ----"
echo "  python3 tools/hwinfo2dts.py hwinfo.json --out openwrt/"
