# RC3000 TTL 救砖指南（bootcmd 卡死恢复 + 固件验证）

> 适用状态：设备上电后不启动系统（卡 U-Boot），env 的 bootcmd 被改为
> `tftpboot 0x44000000 openwrt.itb; bootm 0x44000000`。
> 设备 U-Boot 完好、NAND 原厂系统未动，全程可逆。

## 1. 采购清单（任选其一，几十元）

| 物品 | 说明 |
|---|---|
| USB 转 TTL 模块 | **CH340G** 或 **CP2102** 芯片（搜索"CH340 TTL 串口模块"） |
| 杜邦线 | 母对母 3 根（若模块已焊排针则母对母；否则公对母） |
| 小号十字螺丝刀 | 拆外壳 |

## 2. 拆机与接线

1. 拔电源，拧下底部螺丝拆开外壳；
2. 主板上找 **4 针排针/焊盘**（丝印多为 `J1` / `CON1` / `UART`，靠近板边）；
   四针定义：`VCC(3.3V) / GND / TX / RX`（以丝印为准，**VCC 永远不接**）；
3. 接线（**交叉**）：
   - 模块 `RX`  → 板子 `TX`
   - 模块 `TX`  → 板子 `RX`
   - 模块 `GND` → 板子 `GND`
   - 板子 `VCC` → **悬空不接**（接了可能烧板）
4. 先接线，后上电。

## 3. 电脑连接串口（macOS）

```bash
# 装驱动（CH340）：https://www.wch.cn/downloads/CH34XSER_MAC_ZIP.html
# 插上 USB 模块后确认设备名（一般是 /dev/tty.usbserial-* 或 /dev/tty.wchusbserial*）
ls /dev/tty.* | grep -iE "usb|serial|wch"

# 连接（115200 8N1）。screen 断开：Ctrl+A 再按 K，确认 y
screen /dev/tty.usbserial-XXXX 115200
```

> 若没有 screen 或想更顺手：`brew install minicom && minicom -D /dev/tty.usbserial-XXXX -b 115200`

## 4. 救砖操作序列

路由器上电，串口窗口立刻出现 U-Boot 输出。**在 `Hit any key to stop autoboot`（或 bootdelay 倒数）期间连续按任意键**（回车/Ctrl+C 多按几次），停在 `ipq50xx#` 或类似提示符。

```bash
# 4.1 确认现状
printenv bootcmd

# 4.2 【方案 A：直接恢复原厂启动 —— 最快让路由器回到可用状态】
setenv bootcmd bootipq
saveenv
reset
#    → 设备重启进入原厂系统，先恢复正常使用。之后走第 5 节再补固件验证。

# 4.3 【方案 B：顺手把 RAM 验证做了（推荐）】
#    电脑网线直插路由器 WAN 口，电脑设静态 IP 192.168.1.33/255.255.255.0，
#    电脑终端先跑：
#      sudo /Users/w/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
#        /Users/w/Downloads/rc3000-openwrt/tools/tftp_send.py \
#        --file /Users/w/Downloads/rc3000-openwrt/output/immortalwrt-qualcommax-ipq50xx-h3c_rc3000-initramfs-uImage.itb \
#        --port 69 --bind 192.168.1.33
#    然后 U-Boot 里（TTL）手动敲：
setenv ipaddr 192.168.1.250
setenv serverip 192.168.1.33
tftpboot 0x44000000 openwrt.itb
#    → 看到传输进度和 "Bytes transferred = ..." 即成功
bootm 0x44000000
#    → 内核启动，约 1 分钟后电脑 ssh root@192.168.1.1 验证网口/Wi-Fi

# 4.4 无论 A 还是 B，最后都把 env 恢复原厂（防再次卡死）：
setenv bootcmd bootipq
setenv ipaddr 192.168.10.10
setenv serverip 192.168.10.19
env delete tftpserverport   # 若提示找不到则忽略
env delete fenv_mode        # 同上
saveenv
```

## 5. 验证通过后的正式刷机（可选，写 NAND）

RAM 验证确认网口/双频正常后，再刷 factory 固件长期使用：

```bash
# TTL U-Boot 里（电脑 TFTP 已就绪、文件换 factory.ubi）：
tftpboot 0x44000000 factory.ubi        # 18M，走 69 端口
nand erase 0x3100000 0x2800000         # 擦 rootfs@0x3100000（40MB，mtd16 槽）
nand write 0x44000000 0x3100000 0x2800000
# 【重要】双槽位选择器（实测 BOOTCONFIG 0x80）：0→mtd15(空) 1→mtd16(原厂)
# 刷完槽位选择问题见 PLAN.md；不确定就先只做 RAM 验证，factory 刷机另行规划。
```

## 6. 兜底：救不回来的极端情况

- U-Boot 输出完全乱码 → 波特率不对，依次试 115200 / 57600 / 921600；
- U-Boot 完全无输出 → 接线反了（TX/RX 对调）；再无输出 → 模块驱动没装好；
- U-Boot 提示符进不去（bootdelay=1 太快）→ 上电瞬间开始连敲回车；或事后在 U-Boot 里
  `setenv bootdelay 3; saveenv` 加长窗口。

## 7. 风险与承诺

- 全程不写 NAND（除第 5 节显式刷机外），U-Boot/原厂系统零损伤；
- `saveenv` 只影响 env 参数区（mtd10），原值已备份：`bootcmd=bootipq, ipaddr=192.168.10.10, serverip=192.168.10.19, bootdelay=1`；
- 所有操作都可在 TTL 中即时看到反馈，不再有任何盲区。
