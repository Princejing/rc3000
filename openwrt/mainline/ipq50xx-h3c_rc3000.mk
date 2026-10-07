# H3C Magic RC3000 —— ImmortalWrt 25.12.2 / qualcommax / ipq50xx
#
# 追加到 target/linux/qualcommax/image/ipq50xx.mk
#
# 以 CMCC MR3000D-CI 为底，改了 5 处：型号、IMAGE_SIZE、board 文件、
# reset-gpios（在 DTS 里）、DEVICE_DTS / DEVICE_DTS_CONFIG。
#
# 所有参数来自本机实测（P0 只读侦察）：
#   SoC IPQ5018 + QCN6102(软件名 qcn6122) + QCA8337 + Micron 128MB NAND
#   原厂 DTB compatible = qcom,ipq5018-mp02.1（故 DEVICE_DTS_CONFIG 用 mp02.1）

define Device/h3c_rc3000
	$(call Device/FitImageLzma)
	$(call Device/UbiFit)
	DEVICE_VENDOR := H3C
	DEVICE_MODEL := Magic RC3000
	# DTS 文件: target/linux/qualcommax/files/arch/arm64/boot/dts/qcom/ipq5018-h3c-rc3000.dts
	# 注意: qualcommax 的 Device/Default 已设 DEVICE_DTS_DIR := $(DTS_DIR)/qcom，
	# 框架会自动在文件名前加 qcom/ 子目录，所以这里【不能】再写 qcom/ 前缀，
	# 否则解析成 dts/qcom/qcom/... 导致 "No such file or directory"。
	DEVICE_DTS := ipq5018-h3c-rc3000
	# 本机实测 MP 配置为 mp02.1（原厂 DTB compatible qcom,ipq5018-mp02.1）。
	# 若编译报 "config@mp02.1 not found"，先试 config@mp03.3-m1，再整行删除。
	DEVICE_DTS_CONFIG := config@mp02.1
	SOC := ipq5018
	BLOCKSIZE := 128k
	PAGESIZE := 2048
	# 实测 rootfs / rootfs_1 均为 0x2800000 = 40MB，镜像不能超
	IMAGE_SIZE := 40960k
	NAND_SIZE := 128m
	DEVICE_PACKAGES := ath11k-firmware-ipq5018-qcn6122 \
		ipq-wifi-h3c_rc3000 \
		kmod-dsa-qca8k
endef
TARGET_DEVICES += h3c_rc3000

# ---------------------------------------------------------------------------
# 与模板 CMCC MR3000D-CI 的差异
#
#   1. IMAGE_SIZE   59392k -> 40960k   （本机槽位 40MB）
#   2. reset-gpios  tlmm 39 -> tlmm 26 （在 DTS 里，实测）
#   3. board 文件   ipq-wifi-cmcc_mr3000d-ci -> ipq-wifi-h3c_rc3000
#   4. 型号 / compatible
#   5. DEVICE_DTS / DEVICE_DTS_CONFIG 显式指定（本机 mp02.1）
#
# 待验证（首次编译/启动暴露）：
#
#   A. DEVICE_DTS_CONFIG 的 mp02.1 是否在内核 ipq5018.dtsi 的 config@ 列表里。
#      本机原厂 DTB 是 mp02.1，应匹配；若 dtsi 只有 mp03.x，报错后改 mp03.3-m1。
#
#   B. 无线 BDF 加载（最关键）。DTS 里先按模板写了 qcom,bdf-addr
#      （0x4c400000 / 0x4d100000），前提是 U-Boot 预填该地址。
#      若启动后 ath11k 报 BDF/board 失败：删掉 DTS 里两行 qcom,bdf-addr，
#      改读构造好的 board-2.bin（见 openwrt/board/）。
#
#   C. ipq-wifi 需新增 h3c_rc3000：build.sh 会在 package/firmware/ipq-wifi/
#      放入 board-h3c_rc3000.{ipq5018,qcn6122} 并向 Makefile 的
#      ALLWIFIBOARDS 追加 h3c_rc3000。
#
#   D. 内存压力：本机 256MB RAM。DTS 已用 fw-memory-mode = <1>，实机盯 OOM。
# ---------------------------------------------------------------------------
