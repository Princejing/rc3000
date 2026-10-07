#!/usr/bin/env python3
"""依据路由器上 collect.sh 采集到的 hwinfo.json，生成 RC3000 的板级 DTS
与 OpenWrt 设备定义（ipq50xx.mk 片段、ipq-wifi 条目）。

用法:
    hwinfo2dts.py hwinfo.json --out ./openwrt/
    hwinfo2dts.py --validate openwrt/dts/*.dts
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "openwrt" / "dts"

SOC_FALLBACK = "ipq5018"

# 由 ipq5018.dtsi / ipq5018-ess.dtsi / ipq5018-qcn6122.dtsi 提供，
# 板级 DTS 里以 &label 方式覆写是正常写法，不算错误。
EXTERNAL_LABELS = {
    "sleep_clk", "xo_board_clk", "blsp1_uart1", "prng", "qfprom",
    "qpic_bam", "qpic_nand", "uniphy0", "gmac0", "gmac1", "ge_phy",
    "mdio0", "mdio1", "tlmm", "q6v5_wcss", "wifi", "wifi1", "wifi2",
    "soc", "q6_region",
}


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

def pick_template(switch: str) -> Path:
    if switch.startswith("rtl8367") or switch.startswith("rtl8365"):
        return TEMPLATES / "ipq5018-h3c-rc3000-rtl8367s.dts"
    return TEMPLATES / "ipq5018-h3c-rc3000.dts"


def generate(info: dict, out_dir: Path) -> dict:
    soc = (info.get("soc") or SOC_FALLBACK).lower().replace("ipq5000", "ipq5018")
    switch = (info.get("switch") or "unknown").lower()
    if soc not in ("ipq5018",):
        print(f"警告: 采集到的 SoC 是 {info.get('soc')!r}，本模板只覆盖 IPQ5018；"
              f"IPQ5000 与 IPQ5018 引脚兼容，先按 IPQ5018 生成", file=sys.stderr)

    tpl = pick_template(switch)
    if not tpl.exists():
        raise SystemExit(f"模板不存在: {tpl}")

    dts = tpl.read_text(encoding="utf-8")

    # 交换机无法自动判定时，保留 QCA8337 版本并加显式警告
    if switch == "unknown":
        dts = dts.replace(
            "// ⚠ 三项必须实测确认",
            "// ⚠ 采集时未能判定交换机型号，本文件按 QCA8337 生成，务必先确认",
        )
    dts = dts.replace("// 生成方式: tools/hwinfo2dts.py 依据路由器上 collect.sh 的输出调整本文件。",
                      "// 由 tools/hwinfo2dts.py 依据本机 hwinfo.json 生成。")

    out_dir.mkdir(parents=True, exist_ok=True)
    dts_out = out_dir / "dts" / "ipq5018-h3c-rc3000.dts"
    dts_out.parent.mkdir(parents=True, exist_ok=True)
    dts_out.write_text(dts, encoding="utf-8")

    mk_out = out_dir / "ipq50xx.mk.snippet"
    mk_out.write_text(mk_snippet(switch), encoding="utf-8")

    return {
        "dts": str(dts_out),
        "mk": str(mk_out),
        "switch": switch,
        "soc": soc,
        "nand_mb": info.get("nand_mb"),
        "mem_mb": info.get("mem_mb"),
        "rootfs_mtd": info.get("rootfs_mtd"),
    }


def mk_snippet(switch: str) -> str:
    extra = ""
    if switch.startswith("rtl836"):
        extra = " \\\n\tkmod-dsa-rtl8365mb"

    return f'''# 追加到 target/linux/qualcommax/image/ipq50xx.mk
# （build.sh 会自动把 define..endef 部分追加进去，这里也留一份方便手工操作）
# IMAGE_SIZE 40960k: RC3000 的 rootfs 分区是 0x2800000 = 40MB，镜像不能超过它

define Device/h3c_rc3000
\t$(call Device/FitImage)
\t$(call Device/UbiFit)
\tDEVICE_VENDOR := H3C
\tDEVICE_MODEL := Magic RC3000
\tDEVICE_DTS_CONFIG := config@mp03.1
\tSOC := ipq5018
\tBLOCKSIZE := 128k
\tPAGESIZE := 2048
\tIMAGE_SIZE := 40960k
\tNAND_SIZE := 128m
\tDEVICE_PACKAGES := ath11k-firmware-ipq5018-qcn6122 \\
\tipq-wifi-h3c_rc3000{extra}
endef
TARGET_DEVICES += h3c_rc3000

# package/firmware/ipq-wifi/Makefile 的 ALLWIFIBOARDS 里追加:
#     h3c_rc3000 \\

# 无线校准数据:
#     把路由器上备份出的 ART(0:art) 用 tools/art2board.py 处理，
#     产出 board-h3c_rc3000.bin 放进 package/firmware/ipq-wifi/ 下。
'''


# --------------------------------------------------------------------------
# 校验（本机没有 dtc 时的静态结构检查）
# --------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def validate(path: Path) -> list[str]:
    errs: list[str] = []
    raw = path.read_text(encoding="utf-8")
    code = strip_comments(raw)

    if not code.lstrip().startswith("/dts-v1/;"):
        errs.append("缺少 /dts-v1/; 声明")
    if not code.lstrip().split("\n", 1)[1].lstrip().startswith("#include"):
        errs.append("/dts-v1/; 之后应当是 #include")

    # 花括号配对
    depth, line = 0, 1
    for ch in code:
        if ch == "\n":
            line += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                errs.append(f"第 {line} 行附近出现多余的 }}")
                break
    if depth != 0:
        errs.append(f"花括号未配对，结束时深度为 {depth}")

    # 语句必须以 ; 结束（节点块除外）
    for i, ln in enumerate(code.split("\n"), 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith(";") or s.endswith("{") or s.endswith("}") or s == "}":
            continue
        if s.startswith("}") and ";" in s:
            continue
        # 多行属性（如 interrupts = < ... >;）在结束行才带 ;，中间行跳过
        if "<" in s and ">" not in s:
            continue
        if re.match(r"^[A-Za-z_][\w,.-]*=", s) and ";" not in s and "<" not in s:
            errs.append(f"第 {i} 行属性缺少分号: {s[:60]}")

    # 引用检查: &label 要么在本文件定义，要么来自 dtsi（SoC 层节点）
    defined = set(re.findall(r"^\s*([\w-]+)\s*:\s*[\w@-]+\s*\{", code, flags=re.M))
    defined |= set(re.findall(r"^\s*([\w@-]+)\s*\{", code, flags=re.M))
    for ref in sorted(set(re.findall(r"^&(\w+)\s*\{", code, flags=re.M))):
        if ref not in defined and ref not in EXTERNAL_LABELS:
            errs.append(
                f"引用了未知节点 &{ref}：既不在本文件定义，也不是 ipq5018 已知标签，多半是拼错"
            )

    # 根节点必需属性
    if "model =" not in code:
        errs.append("根节点缺少 model")
    if "compatible =" not in code:
        errs.append("根节点缺少 compatible")

    # phandle 引用完整性（粗查 &xxx 出现在属性里）
    for ref in set(re.findall(r"&([a-z][\w]*)", code)):
        if ref in ("gpio", "1"):
            continue
    return errs


def cmd_gen(args: argparse.Namespace) -> int:
    info = json.loads(Path(args.hwinfo).read_text(encoding="utf-8"))
    r = generate(info, Path(args.out))
    print("生成结果:")
    print(f"  交换机判定 : {r['switch']}")
    print(f"  SoC        : {r['soc']}")
    print(f"  内存       : {r['mem_mb']} MB")
    print(f"  闪存       : {r['nand_mb']} MB")
    print(f"  当前 rootfs: mtd{r['rootfs_mtd']}")
    print(f"  DTS        : {r['dts']}")
    print(f"  设备定义   : {r['mk']}")
    if r["switch"] == "unknown":
        print("\n⚠ 未能自动判定交换机型号，已按 QCA8337 生成。"
              "请在路由器上执行 swconfig list 或 dmesg | grep -iE 'rtl8365mb|qca8k' 确认。")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    failed = 0
    for f in args.files:
        errs = validate(Path(f))
        name = Path(f).name
        if errs:
            failed += 1
            print(f"[FAIL] {name}")
            for e in errs:
                print(f"       - {e}")
        else:
            print(f"[ OK ] {name}  结构检查通过")
    print("\n注意: 这是静态结构检查，不能替代 dtc 编译。"
          "真正编译请在 Linux 环境下执行 build/build.sh（内含 dtc 校验步骤）。")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RC3000 板级 DTS 生成与校验")
    p.add_argument("hwinfo", nargs="?", help="collect.sh 产出的 hwinfo.json")
    p.add_argument("--out", default="./openwrt", help="输出目录")
    p.add_argument("--validate", nargs="+", metavar="DTS", help="改为校验模式")
    args = p.parse_args(argv)

    if args.validate:
        return cmd_validate(args)
    if not args.hwinfo:
        p.error("需要提供 hwinfo.json，或用 --validate 校验模式")
    return cmd_gen(args)


if __name__ == "__main__":
    raise SystemExit(main())
