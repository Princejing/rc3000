# H3C Magic RC3000 → OpenWrt 实施方案

版本：v1（2026-10-07）  
路线：**复用 FlyFish-go 的 QSDK/ipq50xx 代码库，为 RC3000 增加板级配置**  
约束：**免 TTL、不拆机**，走双系统槽位路径  
设备现状：未改动过，可进原厂后台导出配置

---

## 0. 结论摘要

不从头移植。FlyFish-go 的 `H3C-RT3000-Product-R1` 仓库里已经有可工作的  
`target/linux/ipq50xx/` target、RT3000 DTS、ath11k/NSS 补丁集、双槽位工具  
（`rt3bcwrite` / `rt3slot`）和型号级 BDF。RC3000 与 RT3000 同属  
IPQ50xx + QCN6102 平台，正确做法是**在这套已验证的代码上增加 RC3000 板级配置**，  
而不是另起炉灶。

真正的工作量不在「写驱动」，而在**确认你手上这台机器的硬件批次**——  
同型号存在多套 SoC / 交换机 / NAND 组合，这是本方案唯一的高风险环节。

---

## 1. 从参考项目继承的关键事实

来源：`github.com/FlyFish-go/H3C-RT3000-Product-R1` @ main，  
`docs/H3C-IPQ50xx-Hardware-Research-v1.0.md`、`rt3000-machine-b/docs/HARDWARE_SUPPORT.md`、  
`rt3000-machine-b/release/INSTALL.md`、`rt3000-machine-b/patches/board/0001-*.patch`。

### 1.1 同型号存在多套硬件（最重要）

RT3000 的三台同型号样机（内部编号 A/B/C）差异：

|                | A 机                  | B 机（已发布）                     | C 机（未支持）             |
| -------------- | -------------------- | ---------------------------- | -------------------- |
| SoC            | IPQ5018              | **IPQ5000**                  | IPQ5018              |
| RAM            | 独立 Winbond DDR       | 集成 256MB DDR3L               | 独立 GigaDevice DDR    |
| NAND           | Winbond W25N01GWZEIG | **GigaDevice GD5F1GQ5REYIG** | Winbond W25N01GWZEIG |
| NAND ECC / OOB | BCH 4bit / 64B       | **8bit / 128B**              | BCH 4bit / 64B       |
| 交换机            | QCA8337-AL3C         | **QCA8337-AL3C**             | **RTL8367S**         |
| 状态             | 早期改 APPSEL 变砖        | Product R1 r5 基线             | RAM-stage，未完成        |

**推论**：RC3000 必然也存在类似批次差异。交换机若是 RTL8367S，  
难度等同于至今未完成的 C 机；NAND 的 ECC/OOB 写错会直接写坏闪存。

### 1.2 双系统槽位（免 TTL 的基础）

| 槽位 | 分区           | 偏移               | 内容            |
| -- | ------------ | ---------------- | ------------- |
| A  | `rootfs`（原厂） | `0x900000`，40MB  | 出厂系统，**全程不写** |
| B  | `rootfs_1`   | `0x3100000`，40MB | 我们要装的 QSDK    |

selector 是 `0:BOOTCONFIG`(mtd2) 与 `0:BOOTCONFIG1`(mtd3) 中各一份的 4 字节字段：  
`0` → 启动原厂，`1` → 启动 QSDK。切换只改这 8 字节，不重刷、不丢配置。

安全写入顺序：**先把 selector 切回原厂 → 写 mtd16 → 校验通过后才切到 QSDK**。

### 1.3 分区表（RT3000 B 机，需与你的机器核对）

```
0x80000@0         0:SBL1        ro
0x80000@0x80000   0:MIBIB       ro
0x40000@0x100000  0:BOOTCONFIG        ← selector 副本 1
0x40000@0x140000  0:BOOTCONFIG1       ← selector 副本 2
0x100000@0x180000 0:QSEE        ro
0x100000@0x280000 0:QSEE_1      ro
0x40000@0x380000  0:DEVCFG      ro
0x40000@0x3c0000  0:DEVCFG_1    ro
0x40000@0x400000  0:CDT         ro
0x40000@0x440000  0:CDT_1       ro
0x80000@0x480000  0:APPSBLENV   ro
0x140000@0x500000 0:APPSBL      ro
0x140000@0x640000 0:APPSBL_1    ro
0x100000@0x780000 0:ART         ro   ← 射频校准，丢了无线就废
0x80000@0x880000  0:TRAINING    ro
0x2800000@0x900000  rootfs    (40MB) ← 原厂槽
0x2800000@0x3100000 rootfs_1  (40MB) ← 第二槽
0x600000@0x5900000 pdt_data     ro
0x600000@0x5f00000 pdt_data_1   ro
0x100000@0x6500000 exp          ro
0x1700000@0x6600000 plugin      ro
```

注意：社区另一份 RC3000 的 `/proc/mtd` 里 `mtd15=rootfs_1`、`mtd16=rootfs`，  
与 RT3000 的编号相反。**槽位编号必须按偏移确认，不能按编号套。**

### 1.4 两个已踩过的坑（直接继承答案）

1. **UBI volume 名不是默认的 `rootfs`**。原厂要求 `ubi_rootfs`，  
   且 `image-seq` 需固定。他们为此给 `scripts/ubinize-image.sh` 加了  
   `--rootfs-name` 与 `--image-seq` 两个参数（`UBI_ROOTFS_NAME := ubi_rootfs`、  
   `UBI_IMAGE_SEQ := 815647064`）。不这么做镜像起不来。
2. **交换机是「双交换机」模型，不是单 6 口 VLAN**：  
   内部 switch0 出 WAN（eth0），外部 QCA8337 只做 LAN 口扩展且 **VLAN 关闭**，  
   CPU 上行 `6u@eth1`，LAN 口 `2/3/4`。外部交换机复位脚  
   `phy-reset-gpio = <&tlmm 26 0>`；MDIO1 引脚必须配（gpio36=mdc, gpio37=mdio），  
   否则 MDIO 总线不起振、所有读写返回 0x0000。

### 1.5 BDF（无线校准）的正确做法

容器格式（已核实）：

```
0x00   "QCA-ATH11K-BOARD\0"          16B + NUL
header 84 字节，含 board 名（如 bus=ahb,qmi-chip-id=0,qmi-board-id=144）
IE     struct.pack("<II", 1, 0x20000)  出现在 [20,512)
body   0x20000 = 128 KiB
总长   84 + 131072 = 131156
校验   body[0x0A] 处的 LE uint16，使整个 body 按 uint16 逐个 XOR == 0xFFFF
```

**关键教训**：body 是一整块自洽布局。把 A 机器的 modal header 移植到 B 机器的  
payload 上会破坏布局，`WLAN.HK.2.7` 解析天线链时直接 assert。  
正确做法 = **找一个同源完整的 donor payload，只替换自己机器的  
power/calibration 窗口，然后重算 XOR 校验和**。  
他们用的是 Elecom WRC-X3000GS2 的 qcn6122 payload 作 donor，  
替换窗口 `0x2494..0x2758`（RT3000 B 机实测值，RC3000 需自行核对）。

---

## 2. 免 TTL 约束下的风险敞口

不拆机 = 没有串口 = **唯一的底层救援手段不存在**。参考项目自己也写明  
「完整 U-Boot + TFTP NAND 救援未端到端验证」。

因此本方案强制以下红线：

| 红线                       | 理由              |
| ------------------------ | --------------- |
| **绝不写 `mtd15`（原厂槽）**     | 那是唯一的回退路径       |
| **写入前 selector 必须先切回原厂** | 写入失败时设备会尝试启动原厂  |
| **先做完整备份再动任何写操作**        | 没有 TTL，备份是唯一保险  |
| **先 RAM 启动验证，再写 NAND**   | initramfs 断电即恢复 |
| **任何一步输出与预期不符就停**        | 不试探、不重试、先分析     |

免 TTL 下获取硬件信息的手段（都很关键）：

- `cat /proc/mtd` — 分区布局
- `dmesg` — NAND 识别行（`Serial NAND device Manufacturer:...`）、启动配置 `config@mp0x.x`
- **`/proc/device-tree`** — 原厂运行中的 DTB，能直接读出交换机节点、mdio 配置、  
  `phy-reset-gpio`、PHY 地址分配。这是免 TTL 下信息量最大的来源
- `swconfig list` / `ls /sys/class/net` — 交换机型号
- `fw_printenv` — U-Boot 环境变量（productname / mtdparts / bootcmd）

---

## 3. 阶段计划

### P0 · 只读侦察（零风险）

**目标**：建立硬件档案 + 完整备份。不写任何闪存。

**动作**

1. Web 后台 → 基本管理 → 配置管理 → 导出配置，得到 `RC3000.cfg`
2. `python3 tools/cfgtool.py verify RC3000.cfg`
3. `python3 tools/cfgtool.py unlock RC3000.cfg -o RC3000_unlocked.cfg`
4. 导入 `RC3000_unlocked.cfg`，重启；`telnet <设备IP>`，密码 `admin`，`debugshell` 拿 root
5. 在路由器上执行 `sh collect.sh`，另加：
   ```sh
   cat /proc/mtd            > /tmp/mtd.txt
   dmesg                    > /tmp/dmesg.txt
   fw_printenv              > /tmp/uboot_env.txt
   find /proc/device-tree   > /tmp/devicetree.txt
   ```
6. `sh backup.sh <你的电脑IP>`，把 tar 存到电脑上

**产出**：`hwinfo.json` + `/tmp/mtd.txt` + `dmesg.txt` + `devicetree.txt` + 备份 tar

**验收 gate**：备份 tar 存在且 `art.bin` 非零；`cat /proc/mtd` 输出可见。

**中止条件**：配置导入失败（查 BOM/CRLF）、Telnet 连不上、拿不到 root。

### P0 执行记录（2026-10-07）

**设备**：`192.168.1.22`，MAC `40:fe:95:xx:xx:xx`，Web 后台  
`http://192.168.1.22/`（302 → `/mobile.asp`）。不响应 ICMP，但二层可达。  
本机 `192.168.1.33`，同网段。

**配置档案 `RC3000.cfg`**（20,920 字节，1,302 行，纯 LF，无 BOM，MD5 原样通过）：

| 项                                  | 值                                  | 说明                                            |
| ---------------------------------- | ---------------------------------- | --------------------------------------------- |
| 配置头（第 2 行）                         | `RC3000/RC3000V100D007L02`         | **D007** — 配置头字段，**不等于**网页/CLI 软件版本，但指向较低版本区间 |
| `telnetenable`                     | `disable`（`@telnet` 段，行 31，TAB 缩进） | 待改                                            |
| `custconf-common-operator`         | `1000`                             | 中国电信                                          |
| `custconf-common-district`         | `000051`                           | 四川                                            |
| `custconf-strongpwd-status`        | `yes`                              | 强口令开启                                         |
| `custconf-mofang-status`           | `yes`                              | 魔方（四川电信）                                      |
| `custconf-elink-V2.0-status`       | `yes`                              | e-Link V2.0                                   |
| `custconf-ctei-status`             | `yes`                              | CTEI                                          |
| `custconf-eos-status`              | `yes`                              | EOS                                           |
| `custconf-andlink/wolink/pfwu-SDK` | `no`                               | 移动/联通插件，本机不涉及，**不动**                          |

**格式要点（工具已按此修正）**：

- 正文行是 `\t键=值`，**等号两侧无空格**，行首是 TAB
- `custconf-*-status` 用 **`yes`/`no`** 体系，不是 `enable`/`disable`；  
  早期版本把 strongpwd 写成 `disable` 是错的（已修）
- 导入失败最常见原因是 BOM 与 CRLF，`cfgtool.py verify` 会自动提示

**已生成**（两份都通过 MD5 校验）：

| 文件                    | 改动                                                    | 用途               |
| --------------------- | ----------------------------------------------------- | ---------------- |
| `RC3000_telnet.cfg`   | **仅 1 行**：`telnetenable=disable` → `enable`           | **推荐先用这个**，改动面最小 |
| `RC3000_unlocked.cfg` | 上述 + `operator=0000`、`district=000000`、`strongpwd=no` | 可选，动运营商字段        |

差异已核对：保守版与原文件 `diff` 只有第 30 行一处不同，格式逐字节一致。

#### P0 实机结果（已完成）

Telnet 已开（密码 `admin`），`debugshell` 拿到 root（`uid=0(root)`）。

| 门禁项      | 实测值                                                                  | 结论                                                                                |
| -------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| **SoC**  | **IPQ5018** — `qcom,ipq5018-mp02.1`，model `IPQ5018/AP-MP02.1`        | 与 B 机（IPQ5000）**不同**，但 mp 配置同为 `mp02.1`                                           |
| **交换机**  | **QCA AR8337**（switch0=`QCA MP` + switch1=`QCA AR8337`）              | ✅ **不是 RTL8367S**，避开 C 机的坑                                                        |
| **NAND** | **Micron MT29F1G01ABBFDWB-IT**（MID 0x2c）128MiB，page 2048，**OOB 128** | ⚠️ 第三种 NAND（非 GigaDevice / 非 Winbond）；但 `nandeccbits=8 nandspare=128` **与 B 机一致** |
| 内核       | Linux 4.4.60 armv7l（Chaos Calmer 15.05.1）                            | 与 B 机同                                                                            |
| 固件版本     | Version 3.2.0（2021-10-18）                                            | —                                                                                 |
| U-Boot   | `productname=RC3000`、`hardversion=VER.A`、`fctlockflag=1`             | —                                                                                 |
| 无线 BDF   | `cnss2.bdf_integrated=0x10 bdf_pci0=0x50 bdf_pci1=0x60`              | 5G 属 **0x50 family**                                                              |

**分区表与 B 机完全一致**，但有一个**方向性差异**：

```
mtd15  rootfs_1  @0x900000    (40MB)
mtd16  rootfs    @0x3100000   (40MB)
cat /sys/class/ubi/ubi0/mtd_num  ->  16
内核启动参数: ubi.mtd=rootfs
```

→ **当前运行的是 mtd16；mtd15 是空槽。**

|       | RT3000 B 机（教程） | 这台 RC3000 |
| ----- | -------------- | --------- |
| 原厂在跑  | mtd15          | **mtd16** |
| 待写入空槽 | mtd16          | **mtd15** |

🔴 **照抄教程写 `mtd16` 会直接覆盖正在运行的原厂系统，等于当场毁掉唯一回退路径。**  
写入前必须再次确认 `cat /sys/class/ubi/ubi0/mtd_num`，目标槽是**当前不在跑的那个**。

**硬件组合判定**：IPQ5018 + QCA8337 + Micron NAND。参考项目的 A/B/C 三台  
分别是 (IPQ5018+QCA8337+Winbond)、(IPQ5000+QCA8337+GigaDevice)、  
(IPQ5018+RTL8367S+Winbond)——**这台是第四种组合，没有任何已验证先例**。

**备份已完成并校验**：25 个分区镜像，大小与 `/proc/mtd` 逐一相符；  
`art.bin`、`mtd15`、`mtd16` 三个关键镜像的 MD5 与设备端完全一致。  
归档 `backups/RC3000-backup-2026-10-07.tar.gz`（53MB）+ `SHA256SUMS.txt`。

> 踩到的坑：TFTP 块号是 16 位，单个文件**上限 32MiB**（65535×512）。  
> 40MB 的 rootfs 直接传会被静默截断（第一次传出来只有 32MB，MD5 对不上），  
> 必须用 `dd count/skip` 分卷。已把这个限制写进 `tools/tftpd.py` 会自动报警。

### P0 延伸：selector 机制与 BDF（两项都已拿到答案）

#### selector：位置与当前值已确定

`0:BOOTCONFIG`(mtd2) 与 `0:BOOTCONFIG1`(mtd3) **内容逐字节相同**（互为备份），  
结构是一张分区描述表：

```
0x00  magic  a0 a1 a2 a3
0x04  u32    02 00 00 00        (version)
0x08  u32    08 00 00 00        (条目数 = 8)
0x0C  起     8 × 20 字节条目:  name[16] + u32 flag
```

| # | 偏移(name) | 偏移(flag) | 名称         | 当前值              |
| - | -------- | -------- | ---------- | ---------------- |
| 0 | 0x0c     | 0x1c     | `0:QSEE`   | 1                |
| 1 | 0x20     | 0x30     | `0:DEVCFG` | 1                |
| 2 | 0x34     | 0x44     | `0:CDT`    | 1                |
| 3 | 0x48     | 0x58     | `0:APPSBL` | 1                |
| 4 | 0x5c     | **0x6c** | `0:HLOS`   | **0**            |
| 5 | 0x70     | **0x80** | `rootfs`   | **1** ← selector |
| 6 | 0x84     | 0x94     | `0:WIFIFW` | 0                |
| 7 | 0x98     | 0xa8     | `0:BTFW`   | 0                |

**`rootfs` 的 flag 就是 selector，位于 mtd2 / mtd3 的 offset `0x80`，4 字节 LE。**

当前值 = `1`，而实机 `ubi0/mtd_num = 16`（跑的是 mtd16 `rootfs`）  
→ 这台机器的映射是 **`1` → mtd16（原厂），`0` → mtd15（空闲槽）**。

🔴 注意这与参考项目 B 机**数值语义相反**（B 机是 0→原厂 mtd15、1→QSDK mtd16）。  
切换时必须按本机实测关系来：**改 `0x80` 的值为 0，才会去启动 mtd15。**  
mtd2 与 mtd3 必须同时改。`0:HLOS`(0x6c) 是 kernel 槽位，当前 0，含义待验证。

#### BDF：本机专属校准数据已直接提取

`wifi_fw`(mtd22) 在运行时已挂载到 `/lib/firmware/IPQ5018/WIFI_FW`，  
内核参数 `cnss2.bdf_pci0=0x50` 指向 `qcn6122/bdwlan.b50`。  
运行时合成后的校准数据落在 `/tmp`：

| 用途       | 文件                           | 尺寸     | 基准           | 差异                                                                   |
| -------- | ---------------------------- | ------ | ------------ | -------------------------------------------------------------------- |
| **5G**   | `/tmp/qcn6122/caldata_1.bin` | 131072 | `bdwlan.b50` | 仅 77 字节：`0x0a`(校验和) + **`0x272c–0x2777`**(功率/校准) + `0x106e4–0x106f1` |
| **2.4G** | `/tmp/IPQ5018/caldata.bin`   | 131072 | `bdwlan.b10` | 仅 18 字节：`0x0a`(校验和) + **`0x1a00–0x1a1a`**(校准)                        |

两者 XOR 校验和均为 `0xFFFF`（有效 BDF）。  
`caldata_2.bin` 全 `0xFF`——第二路 5G 未使用。

✅ **这意味着不必像参考项目那样跨机器移植 donor 再猜窗口**——  
本机双频的成品 BDF 直接拿到了，且校准窗口的精确位置已知。

已提取归档到 `backups/bdf/`（含 `SHA256SUMS.txt`，MD5 与设备端逐一核对一致）。

完整归档：`backups/RC3000-backup-FULL-2026-10-07.tar.gz`（53MB）。

---

### P1 · 硬件门禁（三项，缺一不可）

| 判定项      | 手段                                             | 通过标准                    |
| -------- | ---------------------------------------------- | ----------------------- |
| **SoC**  | `dmesg` / `grep IPQ /proc/cpuinfo`             | 明确是 IPQ5000 还是 IPQ5018  |
| **交换机**  | `swconfig list`、`/proc/device-tree/soc/mdio@*` | 明确是 QCA8337 还是 RTL8367S |
| **NAND** | `dmesg` 的 `Serial NAND device Manufacturer:`   | 明确型号 + ECC/OOB 参数       |

外加：原厂软件版本、`rootfs` / `rootfs_1` 的**偏移与 mtd 编号对应关系**、  
selector 实际位置、启动配置 `config@mp0x.x`。


**验收 gate（三档分叉）**

| 判定结果                                                   | 后续                                                                |
| ------------------------------------------------------ | ----------------------------------------------------------------- |
| 与 RT3000 B 机完全一致（IPQ5000 + QCA8337 + GigaDevice + 同版本） | **最小改动**：克隆 `h3c,rt3000` 板级配置改名 `rc3000`，改 `compatible`/产品名，其余全复用 |
| 仅 SoC 或 NAND 不同，交换机仍是 QCA8337                          | 改 DTS + NAND 写参数，中等工作量                                            |
| **交换机是 RTL8367S**                                      | ⚠️ 等同于未完成的 C 机路线，**暂停，重新评估**                                      |

---

### P2 · 板级适配

**动作**

1. 拉取参考仓库，在其 `target/linux/ipq50xx/` 下新增 RC3000 板级配置
2. DTS：以 `ipq5000-rt3000.dts`（或 ipq5018，按 P1 结果）为底，改 `compatible`、  
   model；按 P0 采集到的原厂 DTB 校正交换机节点、`phy-reset-gpio`、MDIO 引脚
3. 分区：把 P0 实测的 `mtdparts` 写进 `chosen/bootargs-append`，  
   原厂槽标 `ro` 保护
4. 镜像：`UBI_ROOTFS_NAME := ubi_rootfs`、`UBI_IMAGE_SEQ` 固定，  
   `BLOCKSIZE 128k`、`PAGESIZE 2048`、`IMAGE_SIZE 40960k`、`NAND_SIZE 128m`
5. BDF：用 `art2board.py hybrid`，donor payload + 自己机器的 OEM 窗口 + 重算校验和
6. `package/firmware/ipq-wifi` 增加 `h3c_rc3000`

**产出**：可编译的板级配置 + BDF

**验收 gate**：`make` 能产出 initramfs 与 factory.ubi；BDF 通过 `art2board.py info`  
（长度 131156、body 128K、XOR == 0xFFFF）。

---

### P3 · 构建与验证

1. **先只编 initramfs**，走网络加载做 RAM 启动 —— 不写 NAND，断电即恢复
2. 逐项核对：`/proc/mtd` 是否与原厂一致、`ip link` 网口数量与命名、  
   `dmesg | grep -i ath11k` 双频是否起来、`swconfig` 交换机识别
3. 确认无问题后，才走双槽位写入 mtd16
4. 写入流程：selector 切原厂 → 写 mtd16 → 读回 SHA-256 校验 → 切 QSDK
5. 验证双向切换、保留配置升级

**验收 gate**：QSDK 能启动、双频可用、能切回原厂且原厂仍正常。

---

### P4 · 收尾

- 首次登录后立即设 root 密码，更换默认 Wi-Fi 密码
- 关闭原厂 Telnet 前，先确认回切路径可用
- 备份与硬件档案归档

---

## 3.5 版本选型：ImmortalWrt 25.12.2（推荐）

「越新越好」不等于「最优」。按本机硬件（IPQ5018 + QCN6102 + QCA8337 + 128MB NAND）  
逐个版本比对后的结论：

| 候选                        | 内核       | NSS 硬件加速                             | 有同构模板                   | 评价                        |
| ------------------------- | -------- | ------------------------------------ | ----------------------- | ------------------------- |
| OpenWrt **25.12.5**（最新）   | 6.12     | ❌                                    | ❌（无 MR3000D-CI）         | 最新，但无加速、无直接模板             |
| **ImmortalWrt 25.12.2** ⭐ | **6.12** | **✅ `kmod-qca-nss-dp` + `qca-ssdk`** | **✅ `cmcc_mr3000d-ci`** | **内核与最新同步 + 有加速 + 有同构模板** |
| OpenWrt 24.10.x           | 6.6      | ❌                                    | ❌                       | 内核更老，没有任何优势               |
| QSDK 21.02（参考项目）          | 5.4      | ✅                                    | ❌                       | 内核太老，软件生态差                |

**为什么 ImmortalWrt 25.12.2 是最优解：**

1. **内核 6.12** —— 与 OpenWrt 当前最新的 25.12 系列完全同步，比 24.10 的 6.6 新一整代。
2. **有 NSS/SSDK** —— ImmortalWrt 的 qualcommax 默认包里就带 `kmod-qca-nss-dp`  
   和 `kmod-ath11k-ahb`，`package/kernel/` 下有 `qca-ssdk`、`qca-nss-dp`。  
   这正是主线 OpenWrt 缺失、导致无线吞吐低的那块。
3. **有 `cmcc_mr3000d-ci`** —— 该机 IPQ5018 + QCN6102 + QCA8337 + 128MB NAND，  
   与本机**硬件几乎同构**，可以作为直接模板，工作量从「从零写」降到「改 4 处」。

对照 MR3000D-CI 的 DTS，本机只需改 4 处：

| 项             | MR3000D-CI                 | 本机 RC3000             |
| ------------- | -------------------------- | --------------------- |
| `IMAGE_SIZE`  | 59392k                     | **40960k**（实测槽位 40MB） |
| `reset-gpios` | `&tlmm 39`                 | **`&tlmm 26`**（实测）    |
| board 文件      | `ipq-wifi-cmcc_mr3000d-ci` | `ipq-wifi-h3c_rc3000` |
| 型号            | CMCC MR3000D-CI            | H3C Magic RC3000      |

其余（DSA 交换机模型、QCA8337 `ethernet-switch@17`、3×LAN via phy 0/1/2、  
port6 CPU 上行 SGMII、`fw-memory-mode = <1>`）**完全一致，可直接沿用**。

与 QSDK 路线的差异：

|                 | QSDK（原定） | **主线 25.12.5（现定）**                                            |
| --------------- | -------- | ------------------------------------------------------------- |
| 内核              | 5.4      | **6.12**                                                      |
| NSS/WIFILI 硬件加速 | 有        | **没有** → 无线/有线吞吐会明显低于 QSDK 的 760–879 Mbps                     |
| 无线固件            | 原厂自带     | `ath11k-firmware-ipq5018-qcn6122`（`WLAN.HK.2.7.0.1`）**主线已提供** |
| BDF             | 原厂文件加载   | 需构造 `board-2.bin` 容器（见下）                                      |
| 成熟度             | 同平台已有实机  | 本机为新硬件组合                                                      |

### 主线所需的三项产物（均已完成）

1. **板级 DTS** → `openwrt/mainline/ipq5018-h3c-rc3000.dts`  
   全部参数实测：交换机、MDIO 复位脚、按键、NAND ECC、SMEM 分区。
2. **board-2.bin 容器（双频）** → `openwrt/board/board-h3c_rc3000.{ipq5018,qcn6122}`  
   用本机 caldata 构造，容器结构与参考项目成品逐字节对齐，XOR 校验 `0xFFFF` 有效。  
   board-id：2.4G = **16**（`bdf_integrated=0x10`）、5G = **80**（`bdf_pci0=0x50`）。
3. **设备定义** → `openwrt/mainline/ipq50xx-h3c_rc3000.mk`

### 主线的四个已知风险（编译前必须逐条确认）

1. **BDF 加载方式**——主线 ipq50xx 现有设备一律用 `qcom,bdf-addr` 从预留内存读，  
   依赖 bootloader 预填 `0x4c400000/0x4d100000`。本机原厂走 QSDK cnss 文件加载，  
   **无证据表明 U-Boot 会预填**。已在 DTS 里刻意不写 `bdf-addr`，改走标准  
   firmware 路径读我们的 board-2.bin；若无线起不来再回退。
2. **256MB 内存 + ath11k OOM**——ELECOM WRC-X3000GS2 的 DTS 直接把 Wi-Fi `disabled`，  
   注释理由就是 256MB 上会 OOM。GL-B3000 / SCR50AXE 用 `fw-memory-mode = <1>` 并启用。  
   DTS 已设 `<1>`，需实机盯 OOM。
3. **rootfs 只有 40MB**——`IMAGE_SIZE := 40960k`。主线 6.12 内核 + 基础包可能逼近上限，  
   需要精简包集（去掉 LuCI 大组件或改用 ImageBuilder 现装）。
4. **`DEVICE_DTS_CONFIG`**——主线已有设备的板级 DTS 里并没有 `config@` 节点，  
   但 mk 里却写着 `config@mp03.x`，两者不一致。编译时确认是否可省略。

## 4. 需要你提供的信息

P0 执行完后把这几样给我，我据此生成具体的板级配置：

- [ ] `cat /proc/mtd`
- [ ] `dmesg`（重点看 NAND 识别行和 `config@mp0x.x`）
- [ ] `fw_printenv`
- [ ] `find /proc/device-tree` + 交换机相关节点内容
- [ ] 原厂软件版本（网页后台 / `display version`）
- [ ] 备份 tar 的 SHA-256（校验用）

---

## 5. 不做的事（Out of scope）

- 不改 bootloader、不写 `0:SBL1` / `0:APPSBL`、不执行 `saveenv`
- 不改分区表
- 不写 `mtd15`（原厂槽）
- 不做上游主线 OpenWrt 移植（本轮已选定 QSDK 路线）
- 不做破坏性测试（免 TTL 条件下没有退路）

---

## 6. 已知限制（继承参考项目）

- 100BASE-T / 10BASE-T 未验证；只跑过千兆
- WAN 口物理拔插未验证
- 长时间运行稳定性未验证
- NSS/ECM 卸载性能未调优
- LED 与按键不支持（DTS 未接）
- 没有 OEM Web-OTA，只能走槽位工作流
- **QSDK 启动失败自动回退路径未实测**——不能当作「一定能救回来」

---

## 7. 参考

- `github.com/FlyFish-go/H3C-RT3000-Product-R1` @ main
  - `docs/H3C-IPQ50xx-Hardware-Research-v1.0.md`
  - `rt3000-machine-b/docs/HARDWARE_SUPPORT.md`
  - `rt3000-machine-b/release/{INSTALL,01-GET-TELNET,02-INSTALL-QSDK,03-SWITCH-SYSTEMS,05-RECOVERY,KNOWN_ISSUES}.md`
  - `rt3000-machine-b/patches/board/0001-rt3000-machine-b-board-integration.patch`
  - `rt3000-machine-b/tools/build_qcn6122_hybrid_bdf.py`
- 上游 `openwrt/openwrt` `target/linux/qualcommax/dts/ipq5018-mr3000d-ci.dts`（主线参考）
