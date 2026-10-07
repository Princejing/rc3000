#!/usr/bin/env python3
"""极简 TFTP server —— 只发送（用于免 TTL RAM 验证：U-Boot 从本机拉 initramfs）。

与 tools/tftpd.py（只收不发，用于备份）相反，本脚本处理 RRQ（读请求），
按 512 字节块把文件发给对端，等待 ACK，超时重传。

设计要点：
- 单文件模式（--file）：无论 U-Boot 请求什么文件名，都返回该文件。
  这样 bootcmd 里写 `tftpboot 0x44000000 openwrt.itb` 时，文件名随便起都行。
- 目录模式（--dir）：按 RRQ 文件名的 basename 在目录里找。
- 默认绑 69（TFTP 标准端口，U-Boot 默认连 69，需 sudo）；
  也可用 --port 6969 避开特权端口，但要在 U-Boot 里 `fw_setenv tftpserverport 6969`
  （前提是这台 U-Boot 支持该变量，否则只能绑 69）。

用法:
    sudo python3 tools/tftp_send.py --file output/xxx-initramfs-uImage.itb --port 69
    python3 tools/tftp_send.py --file xxx.itb --port 6969 --bind 192.168.10.3
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

OP_RRQ, OP_DATA, OP_ACK, OP_ERROR = 1, 3, 4, 5
BLOCK = 512
TIMEOUT = 5.0
MAX_RETRY = 12


def send_file(sock: socket.socket, addr: tuple, data: bytes) -> bool:
    """把 data 通过 TFTP 发给 addr。返回是否成功。"""
    total = len(data)
    block = 1
    pos = 0
    retries = 0
    sock.settimeout(TIMEOUT)
    while True:
        chunk = data[pos : pos + BLOCK]
        sock.sendto(struct.pack("!HH", OP_DATA, block) + chunk, addr)
        try:
            ack, _ = sock.recvfrom(1500)
        except socket.timeout:
            retries += 1
            if retries > MAX_RETRY:
                print(f"  ! 对端 {addr[0]}:{addr[1]} 超时过多，放弃 (已发 {pos}/{total})")
                return False
            print(f"  ~ 重传 block {block} (retry {retries})")
            continue
        if len(ack) < 4:
            continue
        aop, ablk = struct.unpack("!HH", ack[:4])
        if aop != OP_ACK:
            continue
        if ablk == block:
            pos += len(chunk)
            retries = 0
            if len(chunk) < BLOCK:
                print(f"  ok 发送完成 {total} bytes ({total/1024/1024:.1f} MiB)")
                return True
            block = 1 if block == 65535 else block + 1
        # ablk != block（重复 ACK 或旧 ACK）：保持当前 block 重发（循环顶部已 sendto）
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="只发送的 TFTP server（用于 RC3000 免 TTL 验证）")
    ap.add_argument("--file", help="要发送的单文件（RRQ 任意名都返回它）")
    ap.add_argument("--dir", help="目录模式：按 RRQ 文件名的 basename 在目录里找")
    ap.add_argument("--port", type=int, default=69)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()

    if not args.file and not args.dir:
        print("需要 --file 或 --dir", file=sys.stderr)
        return 2

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    print(f"TFTP 发送服务已启动: UDP {args.bind}:{args.port}")
    print("等待 U-Boot 的 RRQ（Ctrl-C 结束）…")

    try:
        while True:
            sock.settimeout(None)
            try:
                pkt, addr = sock.recvfrom(1500)
            except KeyboardInterrupt:
                break
            if len(pkt) < 4:
                continue
            op = struct.unpack("!H", pkt[:2])[0]
            if op != OP_RRQ:
                sock.sendto(struct.pack("!HH", OP_ERROR, 2) + b"only RRQ supported\0", addr)
                continue
            # 解析 filename（单文件模式忽略，目录模式用 basename 找）
            fname = pkt[2:].split(b"\0")[0].decode("utf-8", "replace")
            print(f"\n[RRQ] {addr[0]}:{addr[1]} 请求 {fname!r}")
            data = None
            if args.file:
                p = Path(args.file)
                if not p.exists():
                    print(f"  ! 本地文件不存在: {p}")
                    sock.sendto(struct.pack("!HH", OP_ERROR, 1) + b"file not found\0", addr)
                    continue
                data = p.read_bytes()
            else:
                base = os_basename(fname)
                p = Path(args.dir) / base
                if not p.exists():
                    print(f"  ! 目录里找不到: {p}")
                    sock.sendto(struct.pack("!HH", OP_ERROR, 1) + b"file not found\0", addr)
                    continue
                data = p.read_bytes()
            send_file(sock, addr, data)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print("\n已停止。")
    return 0


def os_basename(name: str) -> str:
    import os

    return os.path.basename(name.replace("\\", "/"))


if __name__ == "__main__":
    raise SystemExit(main())
