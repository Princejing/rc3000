#!/usr/bin/env bash
# 构建 H3C Magic RC3000 的 ImmortalWrt 固件（qualcommax / ipq50xx，内核 6.12）。
# 必须在 Linux 下运行（macOS 默认文件系统大小写不敏感，编译 OpenWrt 会失败）。
#
#   ./build.sh                       # 只出 initramfs（内存启动试机用，安全，不写 NAND）
#   ./build.sh --full                # 额外出 factory / sysupgrade（写 NAND 用）
#   ./build.sh --repo <git> --branch <b>
#   ./build.sh --src /path/to/openwrt   # 复用已克隆的源码树
#
# 本机 macOS 无 Docker 时，用 GitHub Actions（见 .github/workflows/build.yml）：
# 把整个工程目录推到你的 GitHub 仓库，Actions 页面点 Run workflow 即可白嫖 CI 编译。
set -euo pipefail

REPO="${REPO:-https://github.com/immortalwrt/immortalwrt.git}"
BRANCH="${BRANCH:-openwrt-25.12}"
SRC=""
FULL=0
JOBS="$(nproc 2>/dev/null || echo 4)"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)   REPO="$2";   shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    --src)    SRC="$2";    shift 2;;
    --full)   FULL=1;      shift;;
    -j|--jobs) JOBS="$2";  shift 2;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0;;
    *) echo "未知参数: $1" >&2; exit 2;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "错误: OpenWrt 必须在 Linux 下编译（大小写敏感文件系统）。" >&2
  echo "  本机可用 Docker:  docker build -t rc3000-build build/ && docker run --rm -v \$PWD:/work rc3000-build" >&2
  echo "  或用 GitHub Actions: 把工程推到你的 GitHub 仓库，Actions 页面点 Run workflow。" >&2
  exit 1
fi

if [[ -z "$SRC" ]]; then
  SRC="$ROOT/build/openwrt-src"
  if [[ ! -d "$SRC/.git" ]]; then
    echo ">>> 克隆 $REPO ($BRANCH)"
    git clone --depth 1 -b "$BRANCH" "$REPO" "$SRC"
  fi
fi

# 25.12 起 qualcommax 的板级 DTS 在 files/arch/arm64/boot/dts/qcom/ 下，
# 文件名约定 ipq5018-<device>.dts。框架的 Device/Default 已设
# DEVICE_DTS_DIR := $(DTS_DIR)/qcom（自动在文件名前加 qcom/ 子目录），
# 所以设备定义里的 DEVICE_DTS 只需写 ipq5018-<device>（不含 qcom/ 前缀）。
DTS_DIR="$SRC/target/linux/qualcommax/files/arch/arm64/boot/dts/qcom"
MK="$SRC/target/linux/qualcommax/image/ipq50xx.mk"
WIFI_MK="$SRC/package/firmware/ipq-wifi/Makefile"

[[ -d "$DTS_DIR" ]] || { echo "错误: 源码里没有 qualcommax DTS 目录，检查 branch 是否正确" >&2; exit 1; }

echo ">>> 安装 DTS (ipq5018-h3c-rc3000.dts)"
cp "$ROOT/openwrt/mainline/ipq5018-h3c-rc3000.dts" "$DTS_DIR/"

# 【强制 DTB 重编】CI 用 actions/cache 缓存整个源码树（含构建产物），
# 实测 run16 出现：DTS 已是新的（fw-memory-mode=<2>），但编出的 DTB 仍是旧值
# （设备树读回 00 00 00 01）——陈旧 .dtb 未被重新生成。
# 仅靠 cp 更新 DTS 的 mtime 并不可靠，这里显式删除所有同名 .dtb 并 touch DTS。
find "$SRC" -name 'ipq5018-h3c-rc3000.dtb' -delete 2>/dev/null || true
touch "$DTS_DIR/ipq5018-h3c-rc3000.dts"
echo ">>> 已清除陈旧 DTB 产物，强制重新生成"

echo ">>> 注册设备定义 (h3c_rc3000)"
if ! grep -q "Device/h3c_rc3000" "$MK"; then
  cat "$ROOT/openwrt/mainline/ipq50xx-h3c_rc3000.mk" >> "$MK"
fi

# ---- 无线校准数据（board-2.bin 容器）——双通道注入 ----
#
# 【教训】run13/14 实机验证：ipq-wifi 包链条（包目录 files/ → PKG_BUILD_DIR
# → install-overlay 的 wildcard）在 CI 上静默失效，设备 /lib/firmware/ath11k/
# 下根本没有 board-2.bin（ath11k fetch 失败 → 无线固件崩溃/QMI 超时）。
#
# 通道 1（决定性）：OpenWrt 官方根 overlay 机制——源码树根 files/ 目录的
#   内容会被原样叠加进 rootfs（initramfs 与 squashfs 同样生效），不经过
#   包管理器与构建目录，没有 wildcard/时序/覆盖等任何中间环节。
FILES_OV="$SRC/files/lib/firmware/ath11k"
mkdir -p "$FILES_OV/IPQ5018/hw1.0" "$FILES_OV/QCN6122/hw1.0"
install -m 0644 "$ROOT/openwrt/board/board-h3c_rc3000.ipq5018" "$FILES_OV/IPQ5018/hw1.0/board-2.bin"
install -m 0644 "$ROOT/openwrt/board/board-h3c_rc3000.qcn6122" "$FILES_OV/QCN6122/hw1.0/board-2.bin"
# 确定性自检：两个 board-2.bin 必须就位且为 131183 字节，否则立即失败（不再静默"成功"）
for _bf in "$FILES_OV/IPQ5018/hw1.0/board-2.bin" "$FILES_OV/QCN6122/hw1.0/board-2.bin"; do
  if [[ ! -s "$_bf" ]] || [[ "$(wc -c < "$_bf")" != "131183" ]]; then
    echo "错误: board-2.bin 注入失败 ($_bf)" >&2
    exit 1
  fi
done
echo ">>> files/ overlay 注入完成（IPQ5018/hw1.0 + QCN6122/hw1.0 各一个 board-2.bin，131183B）"

# 通道 2（双保险保留）：ipq-wifi 包 files/ 目录。若包机制恢复正常，
# 安装的内容与通道 1 相同，覆盖无害。
WIFI_DIR="$(dirname "$WIFI_MK")"
mkdir -p "$WIFI_DIR/files"
cp -f "$ROOT/openwrt/board/board-h3c_rc3000.ipq5018" "$WIFI_DIR/files/" 2>/dev/null || true
cp -f "$ROOT/openwrt/board/board-h3c_rc3000.qcn6122" "$WIFI_DIR/files/" 2>/dev/null || true

# ---- 网口分配（02_network + uci-defaults 双保险）----
# [实测修复] QCA8337 五个 PHY 全部注册为 DSA 口 lan1-lan4（RC3000 丝印
# 与交换机端口号错位，全并入 LAN 保证任意物理口可用），WAN 暂用 eth0
# （dp1/内置 GE PHY）。02_network 在 initramfs/firstboot 由
# config_generate 消费；uci-defaults 在刷写后的首次 boot 再兜底一次。
NET="$SRC/target/linux/qualcommax/base-files/etc/board.d/02_network"
if [[ -f "$NET" ]] && ! grep -q "h3c,rc3000" "$NET"; then
  echo ">>> 注入 02_network case (h3c,rc3000)"
  # 找最后一个顶层 esac（case 块结束行），在其前插入我们的 case。
  LINE="$(grep -n '^esac' "$NET" | tail -1 | cut -d: -f1)"
  if [[ -n "$LINE" ]]; then
    awk -v n="$LINE" 'NR==n{print "h3c,rc3000)"; print "\tucidef_set_interfaces_lan_wan \"lan1 lan2 lan3 lan4\" \"eth0\""; print "\t;;"} {print}' "$NET" > "$NET.tmp" \
      && mv "$NET.tmp" "$NET" \
      || echo "warn: 02_network 注入失败（awk 异常），initramfs 网口可能为默认分配"
  else
    echo "warn: 02_network 中未找到顶层 esac，跳过注入"
  fi
else
  echo ">>> 02_network 已含 h3c,rc3000 或文件缺失，跳过"
fi

# ---- 升级入口修复（platform.sh）——决定 sysupgrade 能否真正写入 ----
# [实测铁证] h3c,rc3000 不在 qualcommax platform_do_upgrade 的 case 列表里，
# 会落到 `*)` 的 default_do_upgrade → `mtd write - "${PART_NAME:-image}"`，
# 而 PART_NAME=firmware；RC3000 的 SMEM 分区表里只有 rootfs/rootfs_1，
# 没有 firmware 分区 → mtd write 失败 → sysupgrade 静默不写入、只重启，
# 表现就是"刷了新镜像但内核/设备树纹丝不动"（曾据此误判 crypto 修复无效）。
# 正确做法：为 h3c,rc3000 加 case 分支，指定 CI_UBIPART=rootfs 走 nand_do_upgrade。
PLAT="$(find "$SRC/target/linux/qualcommax" -path '*lib/upgrade/platform.sh' 2>/dev/null | head -1)"
if [[ -z "$PLAT" ]]; then
  PLAT="$(find "$SRC/package/base-files" -path '*lib/upgrade/platform.sh' 2>/dev/null | head -1)"
fi
if [[ -n "$PLAT" ]]; then
  if ! grep -q "h3c,rc3000" "$PLAT"; then
    echo ">>> 注入 platform.sh case (h3c,rc3000 → nand_do_upgrade)"
    # 在 platform_do_upgrade 的 `*)` 默认分支前插入（取最后一个顶层 `*)`）
    PLINE="$(grep -n '^\s*\*)$' "$PLAT" | tail -1 | cut -d: -f1)"
    if [[ -n "$PLINE" ]]; then
      awk -v n="$PLINE" 'NR==n{print "h3c,rc3000)"; print "\t\tCI_UBIPART=\"rootfs\""; print "\t\tnand_do_upgrade \"$1\""; print "\t\t;;"} {print}' \
        "$PLAT" > "$PLAT.tmp" && mv "$PLAT.tmp" "$PLAT" \
        || echo "warn: platform.sh 注入失败（awk 异常），sysupgrade 将不写入"
    else
      echo "warn: platform.sh 未找到默认分支 \`*)\`，跳过注入"
    fi
  else
    echo ">>> platform.sh 已含 h3c,rc3000，跳过"
  fi
  # 兜底：同步进根 files/ overlay，确保补丁后的脚本一定进 rootfs
  mkdir -p "$SRC/files/lib/upgrade"
  cp -f "$PLAT" "$SRC/files/lib/upgrade/platform.sh" \
    || echo "warn: platform.sh overlay 拷贝失败"
else
  echo "warn: 未找到 lib/upgrade/platform.sh，sysupgrade 可能无法写入"
fi

UCI_DIR="$SRC/target/linux/qualcommax/base-files/etc/uci-defaults"
mkdir -p "$UCI_DIR"
cp -f "$ROOT/openwrt/base-files/uci-defaults/99-h3c-rc3000-network" "$UCI_DIR/" \
  || echo "warn: uci-defaults 拷贝失败"

echo ">>> 注册 ipq-wifi board 包 (h3c_rc3000)"
echo "    —— 必须同时做两步，否则 metadata 扫描报 'Package/ipq-wifi-h3c_rc3000 is missing the TITLE field'："
echo "       1) 加入 ALLWIFIBOARDS 列表；2) 调用 generate-ipq-wifi-package 宏生成带 TITLE 的 Package 定义"
if ! grep -q "h3c_rc3000" "$WIFI_MK"; then
  # 步骤1：在 ALLWIFIBOARDS 列表（以 cmcc_mr3000d-ci 行为锚点）追加 h3c_rc3000。
  # 不依赖前导 tab 匹配，避免 BSD/GNU sed 对 \t 解释不一致：仅匹配列表项独有的
  # "cmcc_mr3000d-ci \"（注意行尾的空格+反斜杠），eval 调用行不含此后缀故不会误中。
  sed -i 's/cmcc_mr3000d-ci \\/cmcc_mr3000d-ci \\\n\th3c_rc3000 \\/' "$WIFI_MK"
fi
if ! grep -q "generate-ipq-wifi-package,h3c_rc3000" "$WIFI_MK"; then
  # 步骤2：在文件末尾的 foreach 展开行之前插入 generate-ipq-wifi-package 调用（提供 TITLE）
  sed -i '/^\$(foreach PACKAGE,$(ALLWIFIPACKAGES)/i $(eval $(call generate-ipq-wifi-package,h3c_rc3000,H3C Magic RC3000))' "$WIFI_MK"
fi

echo ">>> 更新 feeds"
cd "$SRC"
./scripts/feeds update -a
./scripts/feeds install -a

echo ">>> 应用配置"
cp "$ROOT/build/config.seed" .config
make defconfig

# 守卫式校验：确认 h3c_rc3000 已被选中。
# 符号规则（target-metadata.pl L313 铁证）：TARGET_DEVICE_$conf_$profile_id，id 原样小写，
# 无 uc() 清洗 → 正确符号 CONFIG_TARGET_DEVICE_qualcommax_ipq50xx_DEVICE_h3c_rc3000。
# （大写 H3C_RC3000 已实测被 defconfig 丢弃；cmcc 总被编出是因为它是 ipq50xx 默认设备。）
# 放在 if ! 条件里，grep 失败只会让 if 为假，不会因 set -e 神秘退出；
# 若仍未选中则打印 .config 中真实存在的 TARGET_DEVICE 符号，供下一轮精准修正。
if ! grep -qxF "CONFIG_TARGET_DEVICE_qualcommax_ipq50xx_DEVICE_h3c_rc3000=y" .config; then
  echo "错误: h3c_rc3000 未被选中。.config 中现有 TARGET_DEVICE 符号：" >&2
  grep -E '^CONFIG_TARGET_DEVICE_' .config >&2 || echo "(无任何 TARGET_DEVICE 符号)" >&2
  exit 1
fi
echo ">>> 已确认 h3c_rc3000 设备被选中，继续编译"

# 注：独立 DTS 语法校验需内核 dtsi（编译期才就绪），此处跳过；
#     DTS 错误会在下面 make 阶段由内核 dtc 暴露，见 build/build.log。

# 修复 GitHub runner 无法访问 immortalwrt 源站（403/404）的问题：
# 改用 OpenWrt 官方源下载第三方 tarball（已验证 downloads.openwrt.org/sources 含所需文件）。
export MIRROR="https://downloads.openwrt.org/sources"

echo ">>> 预下载源码（独立阶段，失败可清晰定位；优先走 OpenWrt 官方源）"
make download V=s 2>&1 | tail -20 || true

# 关键 host 工具兜底：若某包仍不走 MIRROR，手动预置到 dl/ 让 download.pl 跳过网络
DL="$SRC/dl"; mkdir -p "$DL"
for f in lz4-1.10.0.tar.zst xxHash-0.8.3.tar.zst fakeroot_1.37.1.2.orig.tar.gz; do
  if [ ! -f "$DL/$f" ]; then
    curl -fsSL -o "$DL/$f" "https://downloads.openwrt.org/sources/$f" \
      || echo "warn: 预下载 $f 失败，编译阶段将再尝试"
  fi
done

echo ">>> 开始编译 (make -j$JOBS)"
make -j"$JOBS" V=s 2>&1 | tee "$ROOT/build/build.log"

OUT="$ROOT/output"
mkdir -p "$OUT"
# initramfs 镜像（RAM 启动验证用，tftpboot 加载）
cp -f bin/targets/qualcommax/ipq50xx/*h3c_rc3000*initramfs* "$OUT/" 2>/dev/null || true
# factory / sysupgrade（--full 时；写 NAND 用）
cp -f bin/targets/qualcommax/ipq50xx/*h3c_rc3000* "$OUT/" 2>/dev/null || true

echo
echo "=== 产物 ==="
ls -lh "$OUT"

# 产物自检：若没生成 h3c_rc3000 的 initramfs，说明设备没被编出来，直接报错退出（不再静默"成功"）
if [ -z "$(find "$OUT" -maxdepth 1 -iname '*h3c_rc3000*' -print -quit)" ]; then
  echo "错误: output/ 中没有 h3c_rc3000 镜像，编译产物缺失，终止（请检查设备是否被选中、make 是否真的编了该设备）" >&2
  exit 1
fi

if [[ $FULL -eq 0 ]]; then
  echo
  echo "提示: 默认只编 initramfs（RAM 启动验证）。写 NAND 的 factory 镜像请用 --full。"
fi
echo
echo "下一步: 用 initramfs 通过 U-Boot tftpboot 试启动（详见 PLAN.md 3.6.1 路径 D）。"
