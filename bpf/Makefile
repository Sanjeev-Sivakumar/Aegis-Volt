# Makefile for AURA-QoS eBPF/XDP Kernel Telemetry
# Compiler: clang / llvm
# License: GPL v2

CLANG ?= clang
LLC ?= llc
BPFTOOL ?= bpftool

TARGET := xdp_telemetry.bpf.o
SOURCE := xdp_telemetry.bpf.c

ARCH := $(shell uname -m | sed 's/x86_64/x86/' | sed 's/aarch64/arm64/')

CFLAGS := -O2 -g -Wall -target bpf -D__TARGET_ARCH_$(ARCH) -I/usr/include/$(shell uname -m)-linux-gnu

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CLANG) $(CFLAGS) -c $< -o $@
	@echo "[+] Successfully compiled eBPF XDP hook: $(TARGET)"

clean:
	rm -f $(TARGET)

load: $(TARGET)
	@echo "[+] Attaching XDP program to interface $${IFACE:-veth-wan}..."
	ip link set dev $${IFACE:-veth-wan} xdpgeneric obj $(TARGET) sec xdp

unload:
	@echo "[+] Detaching XDP program from interface $${IFACE:-veth-wan}..."
	ip link set dev $${IFACE:-veth-wan} xdpgeneric off
