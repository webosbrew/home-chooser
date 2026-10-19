# Cross build for webOS (ARM) and the host-side test of the pure policy.
#   make            cross build into ../build/service/openhouse-service
#   make test       build and run test/test-policy natively
#   make check-abi  GLIBC/GLIBCXX versions and SONAMEs the binary needs, against a rootfs
SDK ?= /opt/arm-webos-linux-gnueabi_sdk-buildroot
SYSROOT ?= $(SDK)/arm-webos-linux-gnueabi/sysroot
CROSS ?= $(SDK)/bin/arm-webos-linux-gnueabi-
VERSION ?= $(shell sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' ../app/package.json | head -1)
ROOTFS ?= $(HOME)/Projects/webos-firmwares/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs
OUT ?= ../build/service

CFLAGS ?= -O2 -g0
CFLAGS += -std=gnu11 -Wall -Wextra -Werror -D_FILE_OFFSET_BITS=64 -DOH_VERSION='"$(VERSION)"' $(EXTRA_CFLAGS)
INC = -I$(SYSROOT)/usr/include -I$(SYSROOT)/usr/include/glib-2.0 -I$(SYSROOT)/usr/lib/glib-2.0/include
LIBS = -lluna-service2 -lpbnjson_c -lPmLogLib -lglib-2.0

all: $(OUT)/openhouse-service

$(OUT)/openhouse-service: src/main.c src/policy.c src/policy.h
	mkdir -p $(OUT)
	$(CROSS)gcc --sysroot=$(SYSROOT) $(CFLAGS) $(INC) -o $@ src/main.c src/policy.c $(LIBS) -Wl,-O1,--as-needed
	$(CROSS)strip $@

# Dev build: any luna sender may call it (for luna-send on a test TV). Never ship this one.
dev:
	$(MAKE) OUT=$(OUT)-dev EXTRA_CFLAGS=-DOH_ALLOW_ANY_SENDER

test: test/test-policy
	./test/test-policy

test/test-policy: test/test-policy.c src/policy.c src/policy.h
	$(CC) -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -Isrc -o $@ test/test-policy.c src/policy.c

check-abi: $(OUT)/openhouse-service
	sh test/check-abi.sh $(OUT)/openhouse-service $(ROOTFS)

clean:
	rm -rf $(OUT) $(OUT)-dev test/test-policy

.PHONY: all dev test check-abi clean
