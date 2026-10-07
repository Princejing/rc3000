# TTL / U-Boot 救援与试机

RC3000 是高通 IPQ 平台，**没有 Breed**。Breed 只适用于 MT7621/AR71xx 之类，
在这台机器上不存在，别浪费时间找。底层救援只能靠 TTL + U-Boot + TFTP。

## 1. 接线

主板上有 3~4 个焊盘/排针，标 `VCC TX RX GND`（有些批次要自己焊排针）。

| TTL 模块 | 路由器 |
|---|---|
| TX  | RX  |
| RX  | TX  |
| GND | GND |
| VCC | **不接**（模块由 USB 供电，别给路由器反灌电） |

串口参数：**115200 8N1**，无流控。用 PuTTY / MobaXterm / screen。

```bash
screen /dev/tty.usbserial-XXX 115200      # macOS
# Ctrl-A 然后 K 退出
```

先接好 GND 再通电，通电瞬间窗口里会刷 U-Boot 日志。

## 2. 进 U-Boot 命令行

开机日志里出现 `Hit any key to stop autoboot` 时立刻敲回车。
提示符一般是 `IPQ5018#` 或 `QCA#`。

**如果提示 `### Please input uboot password: ###`**（部分运营商定制机有），
输入时屏幕不回显，盲打 `netpower` 回车——这是高通定制机常见的默认解锁密码。

## 3. 只读地试机（强烈推荐第一步，不写 NAND）

电脑上起 TFTP 服务（Tftpd64 / tftpd-hpa），把 initramfs 放根目录。

```bash
# 电脑固定 IP
sudo ifconfig en0 192.168.1.2/24      # macOS
# 或用网线直连，手动配静态 IP

# U-Boot 里
setenv serverip 192.168.1.2
setenv ipaddr   192.168.1.1
tftpboot 0x44000000 initramfs.bin
bootm 0x44000000
```

能起来就 SSH 进 `192.168.1.1`，核对这些：

```bash
cat /proc/mtd                     # 分区表是否和原厂一致
ip link                           # 网口数量与命名
swconfig list 2>/dev/null         # 交换机型号
dmesg | grep -iE 'ath11k|qcn'     # 5G 是否已枚举
dmesg | grep -iE 'qca8k|rtl8365'  # 交换机驱动是否加载
```

这一步完全不写闪存，断电即恢复，随便试。

## 4. 确认无误后写 NAND

写**对侧**的 rootfs 分区，保留原厂系统做双系统回退。

```bash
# 先看当前从哪个 rootfs 启动
cat /sys/class/ubi/ubi0/mtd_num      # 假设是 16，那就写 15

# 在 initramfs 的 OpenWrt 里
export rootfs=$(cat /proc/mtd | grep rootfs | grep -v _ | cut -d: -f1)
ubidetach -f -p /dev/${rootfs}
ubiformat /dev/${rootfs} -y -f /tmp/openwrt-...-h3c_rc3000-squashfs-factory.ubi
reboot
```

## 5. 变砖了怎么救

只要 TTL 还能进 U-Boot，就能救：

```bash
# 从电脑拉原厂 rootfs 备份（backup.sh 备份出来的那个）
setenv serverip 192.168.1.2
setenv ipaddr   192.168.1.1
tftpboot 0x44000000 rootfs_backup.bin
nand erase 0x900000 0x2800000
nand write 0x44000000 0x900000 0x2800000
reset
```

`0x900000` 和 `0x2800000` 是 RC3000 常见布局（rootfs 起始 0x900000、长度 40MB）。
**刷之前一定用 `smeminfo` 或备份时的 `/proc/mtd` 核对地址**，不同批次会变。

ART 分区（0:art）如果坏了，无线就废了，救回来也要重新做校准，所以：
**动 NAND 之前先把 backup.sh 跑一遍，把 tar 存到电脑上。**
