#!/usr/bin/env python3
"""RC3000 Telnet 客户端（Python 3.13 已移除 telnetlib，这里用裸 socket 实现）。

设计上只做**读取与查询**。除非显式传入 --allow-write，否则任何看起来像
写操作的命令都会被拦下，避免误伤。

用法:
    rtsh.py --probe                                  # 只连不登录，看 banner
    rtsh.py --password admin --cmd "cat /proc/mtd"   # 登录后执行一条命令
    rtsh.py --password admin --script p0.txt         # 批量执行脚本里的命令
    rtsh.py --password admin --shell                 # 交互式（readline）
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time

HOST_DEFAULT = "192.168.1.22"
PROMPT_RE = rb"[>#\$]\s*$|[\r\n][^\r\n]*[>#\$]\s*$"

# 明确禁止的模式（除非 --allow-write）
#
# 判据是「**目标**是闪存」，不是「出现了 dd」。
#   dd if=/dev/mtd13 of=/tmp/art.bin   → 备份，允许
#   dd if=/tmp/x.bin of=/dev/mtd15     → 刷写，禁止
DENY = (
    "nand write", "nand erase", "flash_erase", "ubiformat", "ubiattach",
    "ubidetach", "mtd -r", "mtd write", "mtd erase",
    "of=/dev/mtd", "of=/dev/mtdblock", "> /dev/mtd",
    "fw_setenv", "sysupgrade", "reboot", "halt", "poweroff",
    "rm -rf", "mv /", "passwd",
)


class Telnet:
    def __init__(self, host: str, port: int = 23, timeout: float = 2.0):
        # socket 超时要短：它只决定「一次 recv 阻塞多久」，总等待时长由
        # read(wait=) 控制。设成 120 会让 echo 这种瞬时命令也干等两分钟。
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def _recv(self) -> bytes:
        try:
            d = self.sock.recv(8192)
        except socket.timeout:
            return b""
        if not d:
            raise EOFError("连接已关闭")
        return d

    @staticmethod
    def _strip_iac(data: bytes) -> bytes:
        data = re.sub(rb"\xff[\xf0-\xfe].", b"", data, flags=re.S)
        data = re.sub(rb"\xff[\x00-\xef]", b"", data, flags=re.S)
        return data

    def read(self, wait: float = 1.5, max_bytes: int = 200_000) -> str:
        """读 wait 秒；只要还在出新数据就继续，直到静默或超时。"""
        end = time.time() + wait
        while time.time() < end and len(self.buf) < max_bytes:
            d = self._recv()          # 单次最多阻塞 self.timeout 秒
            if d:
                self.buf += self._strip_iac(d)
                end = time.time() + wait  # 有数据就重新计时
        out, self.buf = self.buf, b""
        return out.decode("utf-8", "replace")

    def expect(self, *patterns: bytes, timeout: float = 15.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.buf
            for p in patterns:
                if p.lower() in chunk.lower():
                    return chunk.decode("utf-8", "replace")
            d = self._recv()
            if d:
                self.buf += self._strip_iac(d)
            elif not chunk:
                time.sleep(0.1)
        raise TimeoutError(
            f"等待 {[p.decode() for p in patterns]} 超时，已收到:\n"
            f"{self.buf.decode('utf-8','replace')[-500:]}"
        )

    def send(self, cmd: str) -> None:
        self.sock.sendall(cmd.encode() + b"\r\n")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def guard(cmd: str, allow_write: bool) -> None:
    if allow_write:
        return
    low = cmd.lower()
    for d in DENY:
        if d in low:
            raise SystemExit(
                f"拒绝执行（含写操作特征 '{d}'）: {cmd}\n"
                "确认无误请用 --allow-write 显式放开。本机当前处于只读侦察阶段。"
            )


def login(t: Telnet, password: str, verbose: bool) -> bool:
    """登录。判定依据是**提示符**，不能靠搜 "password" ——
    banner 里就有 "Use 'passwd' to set your login password!"，会误判。"""
    t.expect(b"Password:", b"assword", b"login:", timeout=15)
    t.buf = b""          # 丢掉登录前的 banner，只统计登录之后的回显
    t.send(password)

    acc = b""
    deadline = time.time() + 12
    while time.time() < deadline:
        acc += t._strip_iac(t._recv() or b"")
        low = acc.lower()
        # 成功：出现 <H3C_xxx> 之类的用户视图提示符
        if b"<h3c" in low or low.rstrip().endswith(b">") or b"]" in acc:
            if verbose:
                print("--- 登录响应 ---")
                print(acc.decode("utf-8", "replace")[-400:])
            return True
        # 失败：再次出现 Password:（第一次是带 ***** 的回显）
        if low.count(b"assword:") >= 2 or b"login failed" in low:
            if verbose:
                print(acc.decode("utf-8", "replace")[-400:])
            return False
        time.sleep(0.2)
    if verbose:
        print(acc.decode("utf-8", "replace")[-400:])
    return False


def enter_shell(t: Telnet, verbose: bool) -> bool:
    t.send("debugshell")
    time.sleep(1.5)
    out = t.read(wait=2.5)
    if verbose:
        print("--- debugshell 响应 ---")
        print(out[-600:])
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="RC3000 Telnet 只读客户端")
    ap.add_argument("--host", default=HOST_DEFAULT)
    ap.add_argument("--port", type=int, default=23)
    ap.add_argument("--password")
    ap.add_argument("--cmd", action="append", default=[], help="可多次传入")
    ap.add_argument("--script", help="每行一条命令的文件")
    ap.add_argument("--shell", action="store_true")
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只连不登录")
    ap.add_argument("--settle", type=float, default=2.5,
                    help="每条命令后等待的秒数（传大文件时调大）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    t = Telnet(args.host, args.port)
    try:
        if args.probe:
            print(t.read(wait=3))
            return 0

        if not args.password:
            print("需要 --password", file=sys.stderr)
            return 2

        if not login(t, args.password, args.verbose):
            print("登录失败：密码不对或设备状态异常。", file=sys.stderr)
            return 1
        print("[已登录]")
        enter_shell(t, args.verbose)

        cmds = list(args.cmd)
        if args.script:
            cmds += [
                l.strip() for l in open(args.script, encoding="utf-8")
                if l.strip() and not l.startswith("#")
            ]

        for c in cmds:
            guard(c, args.allow_write)
            t.send(c)
            time.sleep(0.6)
            print(f"\n===== $ {c} =====")
            print(t.read(wait=args.settle))

        if args.shell:
            print("\n进入交互模式，Ctrl-C 退出。写操作默认被拦。")
            while True:
                try:
                    c = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not c:
                    continue
                guard(c, args.allow_write)
                t.send(c)
                time.sleep(0.5)
                print(t.read(wait=2))
        return 0
    finally:
        t.close()


if __name__ == "__main__":
    raise SystemExit(main())
