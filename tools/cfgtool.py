#!/usr/bin/env python3
"""H3C 家用路由器配置文件(RC3000/RT3000/RW3000/NX30)改写工具。

配置文件格式:
    首行:  "<md5> <内部路径>"      例: 3b52b6f35659d0aeff527baa9668821d /var/run/.tmpcfg
    余下:  明文配置，形如
                @telnet
                telnetenable=disable
                custconf-strongpwd-status=enable

固件的校验方式是对「去掉首行之后的内容」做 MD5，把摘要小写写回首行。
本工具负责: 校验 / 改写 / 重新签名。

用法:
    cfgtool.py verify  rc3000.cfg
    cfgtool.py unlock  rc3000.cfg -o rc3000_unlocked.cfg
    cfgtool.py set     rc3000.cfg telnetenable=enable custconf-common-operator=0000 -o out.cfg
    cfgtool.py resign  rc3000.cfg -o out.cfg
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

DEFAULT_TOKEN = "/var/run/.tmpcfg"

# 解锁项: (配置项, 目标值, 说明)
#
# 取值体系以实机 RC3000(电信) 的配置为准：
#   telnetenable          用 enable / disable
#   custconf-*-status     用 yes / no
# 两者不可混用。andLink / wolink 是移动、联通的插件，电信机上本来就是 no，
# 不去动它——写进不存在或无关的项只会引入无谓的改动面。
UNLOCK_ITEMS: list[tuple[str, str, str]] = [
    ("telnetenable", "enable", "开启 Telnet"),
    ("custconf-strongpwd-status", "no", "关闭强口令机制"),
    ("custconf-common-operator", "0000", "解锁运营商限制的 Web 页面"),
    ("custconf-common-district", "000000", "解锁区域限制"),
]


class CfgError(Exception):
    pass


def split_header(raw: bytes) -> tuple[str, bytes]:
    """拆出首行与正文。返回 (首行token路径, 正文bytes)。"""
    nl = raw.find(b"\n")
    if nl < 0:
        raise CfgError("配置文件只有一行，无法解析（文件损坏或不是 H3C cfg）")

    header = raw[:nl].decode("utf-8", errors="replace").strip()
    body = raw[nl + 1 :]

    parts = header.split()
    if len(parts) < 2 or len(parts[0]) != 32:
        raise CfgError(
            f"首行格式不是 '<32位md5> <路径>'，实际读到: {header!r}\n"
            "    如果你已经手工删掉了首行，请先补一行占位: "
            f"{'0' * 32} {DEFAULT_TOKEN}"
        )
    return parts[1], body


def body_md5(body: bytes) -> str:
    return hashlib.md5(body).hexdigest()


def resign(body: bytes, token: str) -> bytes:
    return f"{body_md5(body)} {token}\n".encode("utf-8") + body


def verify(path: Path) -> bool:
    raw = path.read_bytes()
    token, body = split_header(raw)
    stored = raw[: raw.find(b"\n")].decode("utf-8", errors="replace").split()[0]
    actual = body_md5(body)
    ok = stored.lower() == actual
    print(f"文件     : {path}")
    print(f"内嵌 MD5 : {stored}")
    print(f"实际 MD5 : {actual}")
    print(f"路径标记 : {token}")
    print("结论     : " + ("校验通过" if ok else "校验不一致（改过内容但没重签，固件会拒绝导入）"))

    # 原厂导入失败最常见的两个坑：BOM 和 CRLF
    if raw.startswith(b"\xef\xbb\xbf"):
        print("⚠ 文件带 UTF-8 BOM（efbbbf），设备会拒绝导入。"
              "用 Windows 记事本另存为「UTF-8 无 BOM」，或让本工具重新生成。")
        ok = False
    crlf = raw.count(b"\r\n")
    if crlf:
        print(f"⚠ 文件含 {crlf} 处 CRLF 换行。原厂配置应为纯 LF，"
              "用编辑器改过的话请转成 LF 后重算 MD5。")
        ok = False
    return ok


def set_values(
    body: bytes,
    pairs: list[tuple[str, str]],
    indent: str = "\t",
    append: bool = False,
) -> tuple[bytes, list[str]]:
    """把 key=value 形式的行改成目标值。

    只处理形如 `key=value` 的行（去空白后匹配），**保留原有的行首缩进** ——
    H3C 配置正文的行是带 TAB 缩进的，丢掉缩进会让部分原厂版本拒绝导入。

    append=False（默认）时**只改已存在的键**，不往文件里塞新行。
    原厂配置里没有的字段硬加进去属于无谓改动，可能引发解析器异常。
    确实需要新增时用 append=True 显式开启。

    返回 (新正文, 变更清单)。
    """
    lines = body.split(b"\n")
    wanted = {k: v for k, v in pairs}
    changed: list[str] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if b"=" not in stripped:
            continue
        key = stripped.split(b"=", 1)[0].strip().decode("utf-8", errors="replace")
        if key not in wanted:
            continue
        old_val = stripped.split(b"=", 1)[1].strip().decode("utf-8", errors="replace")
        new_val = wanted[key]
        seen.add(key)
        if old_val == new_val:
            changed.append(f"  = {key} = {new_val} (已是目标值，未改动)")
            continue
        # 保留原始缩进
        lead = line[: len(line) - len(line.lstrip())].decode("utf-8", errors="replace")
        lines[i] = f"{lead}{key}={new_val}".encode("utf-8")
        changed.append(f"  ~ {key}: {old_val} -> {new_val}")

    missing = [k for k in wanted if k not in seen]
    if missing:
        if append:
            if not body.endswith(b"\n"):
                lines.append(b"")
            for k in missing:
                lines.append(f"{indent}{k}={wanted[k]}".encode("utf-8"))
                changed.append(f"  + {k}={wanted[k]} (原文件无此项，已追加)")
        else:
            for k in missing:
                changed.append(f"  - {k} (原文件无此项，跳过; 需要新增请加 --append)")

    return b"\n".join(lines), changed


def cmd_verify(args: argparse.Namespace) -> int:
    return 0 if verify(Path(args.file)) else 1


def cmd_unlock(args: argparse.Namespace) -> int:
    src = Path(args.file)
    token, body = split_header(src.read_bytes())

    items = [(k, v) for k, v, _ in UNLOCK_ITEMS]
    if args.only_telnet:
        items = [("telnetenable", "enable")]
    if args.extra:
        for pair in args.extra:
            if "=" not in pair:
                raise CfgError(f"--extra 需要 key=value 形式，收到: {pair}")
            k, v = pair.split("=", 1)
            items.append((k.strip(), v.strip()))

    new_body, changed = set_values(body, items, append=args.append)
    out = resign(new_body, token)

    dst = Path(args.output) if args.output else src
    if dst == src and not args.in_place:
        backup = src.with_suffix(src.suffix + ".bak")
        shutil.copy2(src, backup)
        print(f"原文件已备份 -> {backup}")
    dst.write_bytes(out)

    print("\n变更清单:")
    for c in changed:
        print(c)
    print(f"\n已重新签名并写出: {dst}")
    print("下一步: Web 后台 -> 基本管理 -> 配置管理 -> 导入配置，然后重启。")
    print("        Telnet 密码默认 admin（改过 WiFi 密码可能同步，两个都试）。")
    print("        登录后输入 debugshell 取得 root。")
    print("        注意: 之后不要执行恢复出厂设置，否则配置会被还原。")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    src = Path(args.file)
    token, body = split_header(src.read_bytes())
    pairs = []
    for pair in args.pairs:
        if "=" not in pair:
            raise CfgError(f"需要 key=value 形式，收到: {pair}")
        k, v = pair.split("=", 1)
        pairs.append((k.strip(), v.strip()))

    new_body, changed = set_values(body, pairs)
    dst = Path(args.output) if args.output else src
    if dst == src and not args.in_place:
        backup = src.with_suffix(src.suffix + ".bak")
        shutil.copy2(src, backup)
        print(f"原文件已备份 -> {backup}")
    dst.write_bytes(resign(new_body, token))
    print("变更清单:")
    for c in changed:
        print(c)
    print(f"已写出: {dst}")
    return 0


def cmd_resign(args: argparse.Namespace) -> int:
    src = Path(args.file)
    token, body = split_header(src.read_bytes())
    dst = Path(args.output) if args.output else src
    dst.write_bytes(resign(body, args.token or token))
    print(f"已重签: {dst} -> MD5 {body_md5(body)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="H3C 配置文件改写与重签名工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("verify", help="校验首行 MD5 是否与正文一致")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("unlock", help="一键开 Telnet 并解除运营商限制")
    sp.add_argument("file")
    sp.add_argument("-o", "--output", help="输出到新文件（默认改写原文件并备份）")
    sp.add_argument("--in-place", action="store_true", help="覆盖原文件且不备份")
    sp.add_argument("--only-telnet", action="store_true", help="只开 Telnet，不动其它项")
    sp.add_argument("--extra", nargs="*", help="额外的 key=value")
    sp.add_argument("--append", action="store_true",
                    help="原文件里没有的键也追加（默认只改已存在的键）")
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser("set", help="按 key=value 精确设置")
    sp.add_argument("file")
    sp.add_argument("pairs", nargs="+")
    sp.add_argument("-o", "--output")
    sp.add_argument("--in-place", action="store_true")
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("resign", help="只重算 MD5，不改内容")
    sp.add_argument("file")
    sp.add_argument("-o", "--output")
    sp.add_argument("--token", help="覆盖首行的内部路径标记")
    sp.set_defaults(func=cmd_resign)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except CfgError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
