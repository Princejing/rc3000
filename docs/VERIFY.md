# H3C RC3000 免 TTL RAM 启动验证 SOP

> 目标：不写 NAND、不改 selector、不断电即回原厂，先在内存里确认 ImmortalWrt 能在这台 RC3000 上跑起来（网口 / 双频）。
> 前提：已编译出 `*-initramfs-uImage.itb`（后台监控会自动下载到 `output/`）。

---

## 0. 原理（为什么能做到免 TTL）

原厂 U-Boot 有 `bootdelay=1` + `Hit Enter to stop autoboot`——**进命令行交互必须 TTL**。
但刷固件 ≠ 进命令行：运行中的原厂系统（telnet 可达，密码 `admin`）带 `fw_setenv`，
可直接改写 U-Boot 环境变量 `bootcmd`。把它改成自动 `tftpboot` 拉镜像，下次开机 U-Boot
**自动执行**，全程不按键、不接 TTL。

已用只读手段坐实可行性：
- `fw_setenv` 存在，`env` 区 `/dev/mtd10` 未标 `ro`，可写
- U-Boot 镜像里 `tftpboot` / `bootm` 命令齐全（完整 TFTP 协议栈）
- `fctlockflag` 不在 U-Boot 代码里 → **不锁 env 写**

本机实测关键事实（来自只读侦察）：
- 设备 telnet：`192.168.1.22`，密码 `admin`
- U-Boot 工厂网络：`192.168.10.x`（ipaddr/serverip 是工厂烧录残留）
- 双槽位：selector 在 `BOOTCONFIG`(mtd2/mtd3) offset `0x80`，当前=`1`→mtd16（原厂），`0`→mtd15（空槽）。**与教程相反**，本验证不碰它。
- RC3000 用**外部 QCA8337** 交换机（cpu_bmp=0x40 / lan_bmp=0x1e / wan_bmp=0），reset gpio `tlmm 26`，ath11k board-id 2.4G=`16` / 5G=`80`。我们的 DTS 据此写，起来后网口应正确。

---

## 1. 一次性准备（每台机器只需一次）

### 1.1 电脑网口临时改到 U-Boot 工厂网络
- IP：`192.168.10.3`（随便取，和 serverip 一致即可）
- 子网掩码：`255.255.255.0`
- 网关：留空
- 用网线把电脑连到 RC3000 的 **LAN 口**（U-Boot 默认从 eth0/LAN 侧起网络）

> 验证完成后记得改回 `192.168.1.x` 才能 SSH 进 OpenWrt（LAN 默认 192.168.1.1）。

### 1.2 确认设备在线
```bash
python3 tools/rtsh.py --host 192.168.1.22 --password admin --cmd "fw_printenv bootcmd"
```

---

## 2. 启动验证（两种方式）

### 方式 A：一键脚本（推荐）
```bash
# 默认绑 69 端口（需 sudo）；--high-port 用 6969 + tftpserverport（若 U-Boot 支持）
tools/verify_ram.sh output/<你的initramfs>.itb --server-ip 192.168.10.3
# 先看不执行的预览：
tools/verify_ram.sh output/<你的initramfs>.itb --server-ip 192.168.10.3 --dry-run
```
脚本会：① 后台起 TFTP 发送服务 → ② 通过 telnet 改 `bootcmd`/`serverip`/`saveenv` → ③ `reboot`。
设备重启后 U-Boot 自动拉镜像进 RAM。

### 方式 B：纯手工（看清每步）
```bash
# (1) 起 TFTP 发送服务（另开一个终端）
sudo python3 tools/tftp_send.py --file output/<你的initramfs>.itb --port 69 --bind 192.168.10.3
# 或免 sudo：python3 tools/tftp_send.py --file ... --port 6969 --bind 192.168.10.3
#   此时还需在 U-Boot env 加：fw_setenv tftpserverport 6969（前提 U-Boot 支持）

# (2) 通过 telnet 改 U-Boot env（本机网口已设 192.168.10.3/24）
python3 tools/rtsh.py --host 192.168.1.22 --password admin --allow-write --settle 3 \
  --cmd "fw_setenv serverip 192.168.10.3" \
  --cmd "fw_setenv ipaddr 192.168.10.2" \
  --cmd "fw_setenv bootcmd 'tftpboot 0x44000000 openwrt.itb; bootm 0x44000000'" \
  --cmd "saveenv" \
  --cmd "reboot"
```
> `rtsh.py` 默认拦 `fw_setenv`/`reboot`，必须加 `--allow-write` 才放行。

---

## 3. 启动后验证清单（OpenWrt 起来后）

设备重启约 30–60 秒进 OpenWrt。**把电脑网口改回 `192.168.1.x`**，然后：

```bash
ssh root@192.168.1.1        # 首次无密码，建议立刻 passwd
# —— 网口 ——
ip link                     # 应看到 eth0/eth1（WAN/LAN），状态 UP，无 NO-CARRIER
ip addr show lan            # LAN 应有 192.168.1.1
# —— 双频 Wi-Fi ——
iw dev                     # 应看到 phy0(2.4G) / phy1(5G)；若只有 phy0 或没有，说明 BDF 没加载（见第 5 节）
logread | grep -i ath11k   # 看驱动是否报错（如 board file / BDF 缺失）
# —— 外网 ——
ping -c3 8.8.8.8
```

**判定标准**：
- ✅ 网口 UP 且能 `ping` 通外网 → 核心移植成功，可进入下一步（刷 NAND）。
- ⚠️ 网口通但 Wi-Fi 起不来 → 正常（board-2.bin 未注入），属下一轮迭代，不影响"系统能跑"结论。
- ❌ 网口全 NO-CARRIER / 起不来 → DTS 交换机配置仍有问题，需回 PLAN 调。

---

## 4. 回退（验证不满意 / 想回原厂）

RAM 验证**断电即回原厂**——只要没执行"写 NAND"步骤，直接拔电再上电，U-Boot 仍按
原厂 `bootcmd` 启动原系统。无需任何操作。

若你已 `saveenv` 改了 `bootcmd` 想清掉：进原厂 telnet（网口改回 192.168.1.x）→
```bash
python3 tools/rtsh.py --host 192.168.1.22 --password admin --allow-write --cmd "fw_setenv bootcmd bootipq" --cmd "saveenv"
```
（原厂 `bootcmd` 是 `bootipq`，改回即可。）

> ⚠️ 唯一风险：若 `bootcmd` 写错导致 U-Boot 起不来，**recovery 才需要 TTL**。
> 所以本验证严格先只做 RAM 启动，确认网口/双频 OK 后再谈写 NAND。

---

## 5. Wi-Fi 校准数据（board-2.bin）补完（验证通过后的下一轮）

当前 25.12.2 的 `ipq-wifi` Makefile 没有把 `files/` 拷进源码树，initramfs 里 **ath11k 大概率缺 BDF**，
Wi-Fi 起不来属预期。补法（已备好工具）：
- 2.4G 校准：`/tmp/IPQ5018/caldata.bin`（本机实测，XOR 0xFFFF）
- 5G 校准：`/tmp/qcn6122/caldata_1.bin`
- 用 `tools/mkboard2.py` 构造 `board-2.bin`（board-id 2.4G=16 / 5G=80），结构已对齐参考项目。

验证通过后，把 board-2.bin 注入重编，Wi-Fi 即可工作。这不影响"系统能启动、网口能通"的验证结论。

---

## 6. 已知坑

| 现象 | 原因 | 处理 |
|------|------|------|
| U-Boot 不拉镜像 / TFTP 超时 | 本机网口没设 192.168.10.x，或 serverip 写错 | 确认网口 IP = serverip；防火墙放行 UDP 69 |
| 绑 69 端口报 Permission denied | 69 是特权端口 | 用 `sudo`，或 `--high-port 6969` + `fw_setenv tftpserverport 6969` |
| 镜像拉到一半卡住 | 超过 32MiB 的 TFTP 块号回绕（initramfs ~15MB 不会踩） | 单文件 < 32MiB 无需分卷 |
| 起来后 SSH 连不上 | 电脑网口还是 192.168.10.x | 改回 192.168.1.x |
| 网口全 NO-CARRIER | DTS 外部 QCA8337 配置错 | 回 PLAN 调 reset-gpio / bmp |
