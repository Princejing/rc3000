#!/usr/bin/env python3
"""处理 RC3000 的 0:ART 分区与 ath11k board-2.bin。

容器结构（已由 FlyFish-go/H3C-RT3000-Product-R1 的实机工作核实）
----------------------------------------------------------------
    offset 0     "QCA-ATH11K-BOARD\\0"     16 字节 + NUL
    header       84 字节，内含 board 名，形如
                 "bus=ahb,qmi-chip-id=0,qmi-board-id=144"
    IE 标记      struct.pack("<II", 1, 0x20000)  出现在 [20, 512) 区间
    body         紧随 IE 标记之后的 0x20000 = 128 KiB 板级数据
    总长         84 + 131072 = 131156 字节

    BDF 校验和   位于 body 偏移 0x0A 的一个 LE uint16，
                 使得整个 body 按 uint16 逐个 XOR 的结果等于 0xFFFF

    关键点：body 是「一整块自洽的布局」。把 A 机器的 modal header 移植到
    B 机器的 payload 上会破坏布局，WLAN.HK.2.7 解析天线链时会 assert。
    正确做法是找一个同源、完整的 donor payload，只替换自己机器的
    power/calibration 窗口，然后重算校验和。

用法
----
    art2board.py info board.bin                     # 看容器结构
    art2board.py extract board.bin -o body.bin      # 取出 128K body
    art2board.py checksum board.bin                 # 校验 XOR
    art2board.py checksum board.bin --fix -o out    # 修校验和
    art2board.py hybrid --base B.bin --oem O.bin --out out.bin
    art2board.py scan art.bin                       # 扫裸 ART 分区
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from functools import reduce
from pathlib import Path

MAGIC = b"QCA-ATH11K-BOARD\0"
BOARD_SIZE = 131156
HEADER_SIZE = 84
BODY_SIZE = 0x20000
ATH11K_DATA_IE = struct.pack("<II", 1, BODY_SIZE)
BDF_CHECKSUM_OFFSET = 0x000A
BDF_CHECKSUM_GOAL = 0xFFFF

# FlyFish-go 在 RT3000 B 机上实测的 OEM power/calibration 窗口。
# RC3000 的窗口位置**必须你自己核对**，不要直接套用。
DEFAULT_PATCH_START = 0x2494
DEFAULT_PATCH_END = 0x2758

# 扫描裸 ART 时关注的线索
SCAN_HINTS = (
    b"QCA-ATH11K-BOARD",
    b"QCA-ATH10K-BOARD",
    b"bus=ahb",
    b"bus=pci",
    b"qmi-board-id=",
    b"variant=",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bdf_xor(body: bytes) -> int:
    if len(body) % 2:
        raise ValueError("BDF body 长度必须是偶数")
    return reduce(int.__xor__, struct.unpack(f"<{len(body) // 2}H", body), 0)


def parse(data: bytes) -> dict:
    """解析 board-2.bin 容器，返回结构描述。失败抛 ValueError。"""
    if not data.startswith(MAGIC):
        raise ValueError(f"不是 ath11k board-2 容器（开头 {data[:16]!r}）")

    ie = data.find(ATH11K_DATA_IE, 20, 512)
    if ie < 0:
        raise ValueError("在 [20,512) 区间找不到 board-data IE 标记")

    body_start = ie + len(ATH11K_DATA_IE)
    body = data[body_start : body_start + BODY_SIZE]

    header = data[: min(HEADER_SIZE, body_start)]
    names = []
    for tok in header.split(b"\0"):
        s = tok.strip()
        if s.startswith(b"bus=") or b"board-id=" in s:
            names.append(s.decode("utf-8", errors="replace"))

    return {
        "magic": MAGIC[:-1].decode(),
        "size": len(data),
        "size_ok": len(data) == BOARD_SIZE,
        "ie_offset": ie,
        "body_start": body_start,
        "body_len": len(body),
        "body_ok": len(body) == BODY_SIZE,
        "names": names,
        "body": body,
        "sha256": sha256(data),
    }


def load_body(path: Path) -> tuple[bytes, bytes]:
    """读一个可能是容器也可能是裸 body 的文件。返回 (body, 原始bytes)。"""
    raw = path.read_bytes()
    if raw.startswith(MAGIC):
        return parse(raw)["body"], raw
    if len(raw) == BODY_SIZE:
        return raw, raw
    raise ValueError(
        f"{path.name}: 既不是 ath11k 容器，长度也不是 {BODY_SIZE}。"
        "如果是裸 body，请确认长度正好 128 KiB。"
    )


def repair_checksum(body: bytearray) -> None:
    body[BDF_CHECKSUM_OFFSET : BDF_CHECKSUM_OFFSET + 2] = b"\0\0"
    ck = bdf_xor(bytes(body)) ^ BDF_CHECKSUM_GOAL
    struct.pack_into("<H", body, BDF_CHECKSUM_OFFSET, ck)
    if bdf_xor(bytes(body)) != BDF_CHECKSUM_GOAL:
        raise RuntimeError("修复后校验和仍不对")


def cmd_info(args: argparse.Namespace) -> int:
    raw = Path(args.file).read_bytes()
    try:
        p = parse(raw)
    except ValueError as e:
        print(f"解析失败: {e}")
        return 1

    print(f"文件        : {args.file}")
    print(f"magic       : {p['magic']}")
    print(f"总长        : {p['size']}  (期望 {BOARD_SIZE}) {'OK' if p['size_ok'] else '不一致'}")
    print(f"IE 标记偏移 : 0x{p['ie_offset']:x}")
    print(f"body 起点   : 0x{p['body_start']:x}  长度 {p['body_len']} "
          f"({'OK' if p['body_ok'] else '长度不对'})")
    print(f"board 名    : {p['names'] or '(header 里没解析到 bus=/board-id=)'}")
    ck = bdf_xor(p["body"])
    print(f"BDF XOR     : 0x{ck:04x}  (期望 0x{BDF_CHECKSUM_GOAL:04x}) "
          f"{'OK' if ck == BDF_CHECKSUM_GOAL else '校验和不对，需要 --fix'}")
    print(f"SHA-256     : {p['sha256']}")
    return 0 if (p["size_ok"] and p["body_ok"] and ck == BDF_CHECKSUM_GOAL) else 1


def cmd_extract(args: argparse.Namespace) -> int:
    body, _ = load_body(Path(args.file))
    Path(args.out).write_bytes(body)
    print(f"已取出 body: {args.out}  ({len(body)} bytes, sha256={sha256(body)})")
    return 0


def cmd_checksum(args: argparse.Namespace) -> int:
    src = Path(args.file)
    raw = src.read_bytes()
    if raw.startswith(MAGIC):
        p = parse(raw)
        body = bytearray(p["body"])
        body_start = p["body_start"]
        out = bytearray(raw)
    else:
        if len(raw) != BODY_SIZE:
            print("裸 body 长度必须是 128 KiB", file=sys.stderr)
            return 2
        body = bytearray(raw)
        body_start = 0
        out = None

    ck = bdf_xor(bytes(body))
    print(f"当前 XOR: 0x{ck:04x}  期望: 0x{BDF_CHECKSUM_GOAL:04x}")
    if ck == BDF_CHECKSUM_GOAL:
        print("校验和正确，无需修复。")
        if args.fix and args.out:
            Path(args.out).write_bytes(bytes(out if out is not None else body))
            print(f"（已按要求原样写出 {args.out}）")
        return 0

    if not args.fix:
        print("校验和不对。加 --fix -o <输出> 修复。")
        return 1

    repair_checksum(body)
    print(f"修复后 XOR: 0x{bdf_xor(bytes(body)):04x}")
    dst = Path(args.out) if args.out else src
    if out is not None:
        out[body_start : body_start + BODY_SIZE] = body
        dst.write_bytes(bytes(out))
    else:
        dst.write_bytes(bytes(body))
    print(f"已写出: {dst}")
    return 0


def cmd_hybrid(args: argparse.Namespace) -> int:
    """用一个完整的 donor payload + 自己机器的 OEM power 窗口合成 BDF。

    base: 结构自洽的 donor 容器（提供 header + body 布局）
    oem : 自己机器上取出的容器/body（提供 power/calibration 数据）
    """
    base_raw = Path(args.base).read_bytes()
    base_p = parse(base_raw)
    base_body = base_p["body"]

    oem_body, _ = load_body(Path(args.oem))

    start, end = args.start, args.end
    if not (0 <= start < end <= BODY_SIZE):
        print(f"窗口范围不合法: 0x{start:x}..0x{end:x}", file=sys.stderr)
        return 2

    window = oem_body[start:end]
    print(f"base    : {args.base}  sha256={sha256(base_raw)}")
    print(f"oem     : {args.oem}  sha256={sha256(Path(args.oem).read_bytes())}")
    print(f"窗口    : 0x{start:x}..0x{end:x}  ({end - start} bytes)")
    print(f"OEM 窗口前 16 字节: {window[:16].hex(' ')}")
    if window == b"\x00" * len(window) or window == b"\xff" * len(window):
        print("⚠ 取出的窗口全 00/FF，八成是偏移不对，请核对后再生成。")

    new_body = bytearray(base_body)
    new_body[start:end] = window
    repair_checksum(new_body)

    out = bytearray(base_raw)
    out[base_p["body_start"] : base_p["body_start"] + BODY_SIZE] = new_body
    result = bytes(out)

    # 自检
    chk = parse(result)
    if bdf_xor(chk["body"]) != BDF_CHECKSUM_GOAL:
        print("自检失败：校验和不对", file=sys.stderr)
        return 1

    Path(args.out).write_bytes(result)
    print(f"\n已生成: {args.out}  (sha256={sha256(result)})")
    print("把它改名为 board-h3c_rc3000.qcn6122 放到 package/firmware/ipq-wifi/。")
    print("⚠ 窗口偏移是 RT3000 B 机的实测值，RC3000 必须自己核对。")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    data = Path(args.art).read_bytes()
    print(f"ART 文件: {args.art}  ({len(data)} bytes, {len(data) / 1024:.0f} KiB)")
    nonzero = sum(1 for b in data if b)
    print(f"非零字节: {nonzero} ({nonzero * 100 // max(len(data), 1)}%)\n")

    hits = []
    for hint in SCAN_HINTS:
        start = 0
        while True:
            i = data.find(hint, start)
            if i < 0:
                break
            bol = data.rfind(b"\n", 0, i) + 1
            eol = data.find(b"\n", i)
            if eol < 0:
                eol = len(data)
            hits.append((bol, hint.decode(), data[bol:eol][:100].decode("utf-8", "replace")))
            start = i + 1

    if not hits:
        print("未找到已知线索。ART 可能被厂商加密或是裸校准数据。")
        print("先把完整分区备份好，再到恩山 / OpenWrt 论坛求助比对。")
        return 1

    seen = set()
    for off, hint, snip in sorted(hits):
        if off // 256 in seen:
            continue
        seen.add(off // 256)
        print(f"  0x{off:08x}  [{hint}]  {snip}")

    print("\nMAC 地址通常在 ART 起始处:")
    for off in (0x0, 0x6, 0xC, 0x12):
        print(f"  0x{off:02x} -> " + ":".join(f"{b:02x}" for b in data[off : off + 6]))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RC3000 ART / ath11k board-2.bin 工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("info", help="解析容器结构")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("extract", help="取出 128K body")
    sp.add_argument("file")
    sp.add_argument("-o", "--out", default="body.bin")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("checksum", help="校验/修复 BDF XOR 校验和")
    sp.add_argument("file")
    sp.add_argument("--fix", action="store_true")
    sp.add_argument("-o", "--out")
    sp.set_defaults(func=cmd_checksum)

    sp = sub.add_parser("hybrid", help="donor payload + 自己的 OEM 窗口合成 BDF")
    sp.add_argument("--base", required=True, help="结构自洽的 donor 容器")
    sp.add_argument("--oem", required=True, help="自己机器上取出的容器或裸 body")
    sp.add_argument("--out", required=True)
    sp.add_argument("--start", type=lambda s: int(s, 0), default=DEFAULT_PATCH_START)
    sp.add_argument("--end", type=lambda s: int(s, 0), default=DEFAULT_PATCH_END)
    sp.set_defaults(func=cmd_hybrid)

    sp = sub.add_parser("scan", help="扫描裸 ART 分区")
    sp.add_argument("art")
    sp.set_defaults(func=cmd_scan)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
