# H3C Magic RC3000 → OpenWrt 移植工程

把电信定制的 H3C Magic RC3000（IPQ5018/IPQ5000 + QCN6102 + 128MB SPI-NAND）
跑到主线 OpenWrt（`qualcommax/ipq50xx`）上的一整套工具与板级适配。

## 硬件事实

| 项 | 官网标称 | 拆机实测 |
|---|---|---|
| SoC | IPQ5000 双核 A53 1.0GHz + NPU | **IPQ5000 或 IPQ5018**（看批次） |
| 内存 | 256MB DDR | 256MB DDR3 |
| 闪存 | 未标 | **128MB SPI-NAND** |
| 2.4G | SoC 集成 2x2 574Mbps | 同 |
| 5G | 2x2 2402Mbps 160MHz | **QCN6102**（PCIe，ath11k 里当作 qcn6122） |
| 交换机 | 4×千兆 | **QCA8337 或 RTL8367S**（看批次，必须自己确认） |

OpenWrt 官网**没有** RC3000。但 `qualcommax/ipq50xx` 子目标已经支持 IPQ5018，
最接近的已适配机型是 **CMCC MR3000D-CI**（同为 IPQ5018 + QCN6102 + QCA8337 + SPI-NAND），
本工程的 DTS 就是照着它改的。

## 目录

```
tools/cfgtool.py       改配置文件开 Telnet / 解除运营商限制（本机就能跑）
tools/hwinfo2dts.py    按采集结果生成 DTS + 设备定义；含 DTS 结构校验
tools/art2board.py     处理 ART 分区，产出 ath11k board-2.bin
router/collect.sh      在路由器上跑，采集真实硬件信息 → hwinfo.json
router/backup.sh       全分区备份（含 ART），唯一的救砖保险
openwrt/dts/           两个交换机变体的板级 DTS
openwrt/ipq50xx.mk.snippet  OpenWrt 设备定义
build/                 Docker 构建环境 + 一键构建脚本 + GitHub Actions
rescue/uboot.md        TTL 接线、U-Boot 试机、救砖命令
tests/                 工具自测（15 项）
```

## 操作流程

### 第 1 步 · 开 root shell（本机操作，零风险）

Web 后台 → 基本管理 → 配置管理 → **备份配置**，拿到 `RC3000.cfg`：

```bash
python3 tools/cfgtool.py verify RC3000.cfg          # 先看校验是否对得上
python3 tools/cfgtool.py unlock RC3000.cfg -o RC3000_unlocked.cfg
```

工具会打开 Telnet、关掉强口令、解运营商/区域锁，并**重算首行 MD5**。
把 `RC3000_unlocked.cfg` 在 Web 后台导入，重启。

- Telnet 登录密码默认 **`admin`**（若改过 WiFi 密码可能同步，两个都试）
- 登录后输入 `debugshell` 拿 root
- **之后千万别恢复出厂设置**，否则配置被还原
- 导入失败先查 BOM / CRLF —— `cfgtool.py verify` 会自动提示

> ⚠️ 本 README 描述的是**主线 OpenWrt 移植**路线。实际采用的路线是
> **复用 FlyFish-go 的 QSDK/ipq50xx 代码库 + 免 TTL 双槽位**，
> 详见 [PLAN.md](PLAN.md)。

### 第 2 步 · 备份（路由器上，动闪存前必做）

```bash
sh backup.sh 192.168.1.2        # 参数是你电脑的 IP（需开 tftp 服务）
```

产物 `rc3000-backup.tar`，**务必存到电脑上**。
里面的 `art.bin` 是射频校准数据，丢了无线就废了。

### 第 3 步 · 采集硬件信息

```bash
sh collect.sh                   # 输出 /tmp/hwinfo.json
tftp -p -l /tmp/hwinfo.json -r hwinfo.json 192.168.1.2
```

回到电脑：

```bash
python3 tools/hwinfo2dts.py hwinfo.json --out ./openwrt
```

会自动判别交换机型号，选对应的 DTS 变体。判不出来会按 QCA8337 生成并明确警告，
这时手工执行 `swconfig list` 或 `dmesg | grep -iE 'rtl8365mb|qca8k'` 确认。

### 第 4 步 · 焊 TTL

见 `rescue/uboot.md`。**没有 Breed**——IPQ 平台不适用，底层救援只能靠 TTL。
115200 8N1，TX/RX 交叉，别接 VCC。

### 第 5 步 · 编译

OpenWrt 必须在 Linux 下编译（macOS 文件系统默认大小写不敏感）。三选一：

```bash
# A. 有 Linux 机器
build/build.sh --branch openwrt-24.10

# B. 用 Docker
docker build -t rc3000-build build/
docker run --rm -v "$PWD":/work -w /work rc3000-build build/build.sh

# C. 白嫖 GitHub Actions
#    把 .github/workflows/build.yml 推到自己的仓库，Actions 里手动触发
```

先只编 **initramfs**（默认），不要一上来就出 factory 镜像。

### 第 6 步 · 内存试机（不写闪存）

```bash
setenv serverip 192.168.1.2
setenv ipaddr   192.168.1.1
tftpboot 0x44000000 initramfs.bin
bootm 0x44000000
```

起来后核对 `/proc/mtd`、`ip link`、`dmesg | grep -i ath11k`。
这步断电即恢复，随便试。

### 第 7 步 · 写 NAND

```bash
export rootfs=$(cat /proc/mtd | grep rootfs | grep -v _ | cut -d: -f1)
ubidetach -f -p /dev/${rootfs}
ubiformat /dev/${rootfs} -y -f /tmp/openwrt-...-h3c_rc3000-squashfs-factory.ubi
reboot
```

写对侧分区，原厂系统留着做回退。

## 已知待确认项

DTS 里有 `[VERIFY]` 标记的三处必须实测：

1. **按键 / LED 的 GPIO** — 现在用的是参照板的值。不对只影响按键和灯，不影响启动。
2. **交换机型号** — QCA8337 / RTL8367S 两个变体都给了，按 `collect.sh` 结果选。
3. **`qcom,bdf-addr`** — 无线校准数据在预留内存里的地址，先用同平台值。
   如果 2.4G/5G 起不来，在原厂固件的 dmesg 里搜 `bdf` / `ath11k` 找真值。

另外无线校准文件 `board-h3c_rc3000.bin` 需要你自己从 ART 生成：

```bash
python3 tools/art2board.py scan  art.bin        # 先看结构
python3 tools/art2board.py build art.bin        # 调 ath10k-bdencoder 打包
```

**省事办法**：RC3000 与 CMCC MR3000D-CI 硬件几乎一样，先临时改用
`ipq-wifi-cmcc_mr3000d-ci` 并把 DTS 里的 `qcom,ath11k-calibration-variant`
改成 `"CMCC-MR3000D-CI"`，大概率无线直接就能出。

## 自测

```bash
python3 tests/test_tools.py     # 15 项
```

注意：DTS 的静态结构检查不能替代 `dtc` 编译，真正编译会走 `build/build.sh`
里的 dtc 校验步骤（需要 Linux + device-tree-compiler）。

## 风险

刷机可能变砖。备份、TTL、电源稳定，缺一不可。
电信 RC3000 升到 **008 之后无法降级**，这是单向门，动手前先确认版本号。
