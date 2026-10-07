# RC3000 OpenWrt 构建环境（Linux 容器）
#
#   docker build -t rc3000-build build/
#   docker run --rm -v "$PWD":/work -w /work rc3000-build build/build.sh
#
# 编译产物会落在宿主机的 ./output 与 ./build/openwrt-src

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
        ack antlr3 asciidoc autoconf automake autopoint binutils bison \
        build-essential bzip2 ccache clang cmake cpio curl device-tree-compiler \
        ecj fastjar flex gawk gettext git gperf haveged helpman intltool \
        libelf-dev libglib2.0-dev libgmp3-dev libltdl-dev libmpc-dev \
        libmpfr-dev libncurses5-dev libncursesw5-dev libreadline-dev \
        libssl-dev libtool lld llvm lrzsz mkisofs msmtp nano ninja-build \
        p7zip p7zip-full patch pkgconf python3 python3-pip python3-ply \
        python3-setuptools qemu-utils re2c rsync scons squashfs-tools \
        subversion swig texinfo uglifyjs upx-ucl unzip vim wget xmlto \
        xxd zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# OpenWrt 构建不允许用 root 跑（部分脚本会报错）
RUN useradd -m -s /bin/bash builder \
    && mkdir -p /work && chown -R builder:builder /work

USER builder
WORKDIR /work

CMD ["build/build.sh"]
