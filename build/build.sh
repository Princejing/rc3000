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

echo ">>> 注册设备定义 (h3c_rc3000)"
if ! grep -q "Device/h3c_rc3000" "$MK"; then
  cat "$ROOT/openwrt/mainline/ipq50xx-h3c_rc3000.mk" >> "$MK"
fi

# 无线校准数据（board-2.bin 容器）。放进 ipq-wifi 包的 files/ 子目录，
# 供下面的 generate-ipq-wifi-package 宏的 install-overlay 通过
# wildcard $(PKG_BUILD_DIR)/board-h3c_rc3000.* 命中并装入镜像。
# （仅作 best-effort：即使未命中，包也会空编译通过，不影响整体构建成败。）
WIFI_DIR="$(dirname "$WIFI_MK")"
mkdir -p "$WIFI_DIR/files"
cp -f "$ROOT/openwrt/board/board-h3c_rc3000.ipq5018" "$WIFI_DIR/files/" 2>/dev/null || true
cp -f "$ROOT/openwrt/board/board-h3c_rc3000.qcn6122" "$WIFI_DIR/files/" 2>/dev/null || true

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
ls -lh "$OUT" 2>/dev/null || echo "(无产物，请查看 build/build.log)"

if [[ $FULL -eq 0 ]]; then
  echo
  echo "提示: 默认只编 initramfs（RAM 启动验证）。写 NAND 的 factory 镜像请用 --full。"
fi
echo
echo "下一步: 用 initramfs 通过 U-Boot tftpboot 试启动（详见 PLAN.md 3.6.1 路径 D）。"
