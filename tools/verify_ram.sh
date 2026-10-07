#!/usr/bin/env bash
# =============================================================================
# 免 TTL RAM 启动验证编排脚本（H3C RC3000 → ImmortalWrt）
#
# 原理：运行中的原厂系统带 fw_setenv，可改写 U-Boot 环境变量。
#   把 bootcmd 改成 "tftpboot 0x44000000 openwrt.itb; bootm 0x44000000" +
#   设好 serverip/ipaddr，下次开机 U-Boot 自动从本机 TFTP 拉 initramfs 进 RAM
#   启动 OpenWrt —— 不写 NAND、不改 selector、断电即回原厂。
#
# 前置：
#   1) 已编译出 initramfs 镜像（*-initramfs-uImage.itb），传作 $1
#   2) 本机已装 python3，tools/ 下有 tftp_send.py / rtsh.py
#   3) 设备 telnet 可达（默认 192.168.1.22，密码 admin）
#
# 用法：
#   tools/verify_ram.sh output/xxx-initramfs-uImage.itb --server-ip 192.168.10.3
#   tools/verify_ram.sh output/xxx.itb --server-ip 192.168.10.3 --dry-run   # 只打印命令
#   tools/verify_ram.sh output/xxx.itb --server-ip 192.168.10.3 --high-port  # 用 6969 + tftpserverport
# =============================================================================
set -eo pipefail
# 注：不用 set -u。本脚本变量均有默认赋值；在 WorkBuddy shell shim 下
# set -u 会对 source 作用域内的同段变量产生误判（一处可用一处 unbound），徒增诡异报错。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ITB="${1:-}"
shift || true
SERVER_IP=""
DRY=0
HIGH_PORT=0
DEV_IP="192.168.1.22"
PASSWORD="admin"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-ip) SERVER_IP="$2"; shift 2 ;;
    --dry-run)    DRY=1; shift ;;
    --high-port)  HIGH_PORT=1; shift ;;
    --dev-ip)     DEV_IP="$2"; shift 2 ;;
    --password)   PASSWORD="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$ITB" || ! -f "$ITB" ]]; then
  echo "用法: $0 <initramfs.itb> --server-ip 192.168.10.3 [--dry-run] [--high-port]" >&2
  exit 2
fi
if [[ -z "$SERVER_IP" ]]; then
  echo "必须指定 --server-ip（本机在 U-Boot 工厂网络侧的 IP，如 192.168.10.3）" >&2
  exit 2
fi

TNFTP="$ROOT/tools/tftp_send.py"
RTSH="$ROOT/tools/rtsh.py"
[[ -f "$TNFTP" && -f "$RTSH" ]] || { echo "缺少 tools/tftp_send.py 或 tools/rtsh.py" >&2; exit 1; }

PORT=69
if [[ "$HIGH_PORT" -eq 1 ]]; then
  PORT=6969
  TFTPSERVERPORT_CMD="fw_setenv tftpserverport 6969"
else
  TFTPSERVERPORT_CMD="# (特权端口 69，无需 tftpserverport)"
  # 绑 69 需要 root
  if [[ $EUID -ne 0 ]]; then
    echo "提示：绑 69 端口需要 root，将用 sudo 启动 TFTP server。" >&2
  fi
fi

# ---- 打印将要执行的动作（dry-run 也走这里） ----
echo "==================================================================="
echo " 免 TTL RAM 验证预览"
echo " initramfs : $ITB ($(du -h "$ITB" | cut -f1))"
echo " 设备 telnet: $DEV_IP (密码 $PASSWORD)"
echo " 本机 TFTP  : $SERVER_IP:$PORT"
echo "==================================================================="

CMDS=(
  "fw_setenv serverip $SERVER_IP"
  "fw_setenv ipaddr 192.168.10.2"
)
if [[ "$HIGH_PORT" -eq 1 ]]; then
  CMDS+=("fw_setenv tftpserverport 6969")
fi
CMDS+=(
  "fw_setenv bootcmd 'tftpboot 0x44000000 openwrt.itb; bootm 0x44000000'"
  "fw_setenv fenv_mode 1"
  "saveenv"
)

if [[ "$DRY" -eq 1 ]]; then
  echo "【dry-run】以下步骤将被执行（不连接设备、不起 TFTP、不重启）："
  echo "--- 步骤 A: 本机起 TFTP 发送服务 ---"
  echo "    python3 $TNFTP --file '$ITB' --port $PORT --bind $SERVER_IP &"
  echo "--- 步骤 B: 通过 telnet 改 U-Boot env（需你把电脑网口设为 $SERVER_IP/24，能连 $DEV_IP）---"
  for c in "${CMDS[@]}"; do
    echo "    telnet> $c"
  done
  echo "    telnet> reboot"
  echo "--- 步骤 C: 设备重启后 U-Boot 自动 tftpboot 拉镜像进 RAM ---"
  echo "--- 步骤 D: 把电脑网口改回 192.168.1.x，SSH 进 OpenWrt (192.168.1.1) 验证 ---"
  exit 0
fi

# ---- 真执行 ----
echo
echo ">>> 步骤 A: 起 TFTP 发送服务（后台）"
PYBIN="$(command -v python3 || true)"
[[ -x "$PYBIN" ]] || PYBIN="$(cd "$ROOT" && ls ../.workbuddy/binaries/python/versions/*/bin/python3 2>/dev/null | head -1)"
if [[ "$HIGH_PORT" -eq 1 ]]; then
  "$PYBIN" "$TNFTP" --file "$ITB" --port "$PORT" --bind "$SERVER_IP" &
else
  sudo "$PYBIN" "$TNFTP" --file "$ITB" --port "$PORT" --bind "$SERVER_IP" &
fi
TFTP_PID=$!
sleep 1
echo "    TFTP server PID=$TFTP_PID（验证完 Ctrl-C / kill $TFTP_PID 关闭）"

echo
echo ">>> 步骤 B: 通过 telnet 改 U-Boot env（请确认电脑网口已是 $SERVER_IP/24，且能 ping 通 $DEV_IP）"
CMDS_STR=$(printf '%s\n' "${CMDS[@]}")
# 用 rtsh.py --allow-write 放行 fw_setenv/reboot
"$PYBIN" "$RTSH" --host "$DEV_IP" --password "$PASSWORD" --allow-write \
  --settle 3 --cmd "$(echo "$CMDS_STR" | sed '1p')" \
  --cmd "$(echo "$CMDS_STR" | sed -n '2p')" \
  --cmd "$(echo "$CMDS_STR" | sed -n '3p')" \
  --cmd "$(echo "$CMDS_STR" | sed -n '4p')" \
  --cmd "$(echo "$CMDS_STR" | sed -n '5p')" \
  --cmd "$(echo "$CMDS_STR" | sed -n '6p')" \
  --cmd "reboot"

echo
echo ">>> 步骤 C: 设备正在重启，U-Boot 会自动 tftpboot 拉 initramfs 进 RAM 启动"
echo ">>> 步骤 D: 启动后请把电脑网口改回 192.168.1.x，SSH 进 OpenWrt (192.168.1.1) 验证"
echo "    验证清单见 docs/VERIFY.md。验证完别忘了 kill $TFTP_PID 关 TFTP 服务。"
