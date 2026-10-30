# Cross build for webOS (ARM) and the host-side test of the pure policy.
#   make            cross build into ../build/service/homechooser-service
#   make test       build and run test/test-policy natively
#   make check-abi  GLIBC/GLIBCXX versions and SONAMEs the binary needs, against a rootfs
#
# The build machine's paths are variables with these defaults; set any of them on the command line
# (make SDK=/path) or in the environment (docs/settings-app.md, "Build and test").
#   SDK        the webOS buildroot SDK (arm-webos-linux-gnueabi)
#   SYSROOT    its sysroot; CROSS the tool prefix (gcc, strip, readelf, nm)
#   FIRMWARES  a directory of unpacked firmware images (else OPENHOUSE_FIRMWARES, as the Home key hook
#              tests read it); ROOTFS the rootfs check-abi reads (5.6.1); KEYFILTER_ROOTFS the 6-9
#              rootfs whose libraries and systemUi.js test-homekey runs against (7.x)
#   VERSION    the version compiled in: app/package.json's, the one source of the version
SDK ?= /opt/arm-webos-linux-gnueabi_sdk-buildroot
SYSROOT ?= $(SDK)/arm-webos-linux-gnueabi/sysroot
CROSS ?= $(SDK)/bin/arm-webos-linux-gnueabi-
VERSION ?= $(shell sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' ../app/package.json | head -1)
FIRMWARES ?= $(or $(OPENHOUSE_FIRMWARES),$(HOME)/Projects/webos-firmwares)
ROOTFS ?= $(FIRMWARES)/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs
KEYFILTER_ROOTFS ?= $(FIRMWARES)/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs
OUT ?= ../build/service

CFLAGS ?= -O2 -g0
CFLAGS += -std=gnu11 -Wall -Wextra -Werror -D_FILE_OFFSET_BITS=64 -DGLIB_VERSION_MIN_REQUIRED=GLIB_VERSION_2_40 -DGLIB_VERSION_MAX_ALLOWED=GLIB_VERSION_2_40 -DHC_VERSION='"$(VERSION)"' $(EXTRA_CFLAGS)
INC = -Isrc -I$(SYSROOT)/usr/include -I$(SYSROOT)/usr/include/glib-2.0 -I$(SYSROOT)/usr/lib/glib-2.0/include
LIBS = -lluna-service2 -lpbnjson_c -lPmLogLib -lglib-2.0
SOURCES = src/main.c src/json.c src/luna.c src/proc.c src/fs.c src/policy.c \
	src/apps.c src/bootlink.c src/notify.c src/homekey-main.c src/homekey.c src/homekey-generator.c \
	src/service/main.c src/service/reply.c src/service/files.c src/service/call.c \
	src/service/run.c src/service/launcher.c src/service/system.c
HEADERS = $(wildcard src/*.h src/service/*.h) src/homekey-shapes.inc

all: $(OUT)/homechooser-service

$(OUT)/homechooser-service: $(SOURCES) $(HEADERS) Makefile ../app/package.json
	@test -n "$(VERSION)" || { echo 'no version: app/package.json has no "version" (or set VERSION=)' >&2; exit 1; }
	@test -x "$(CROSS)gcc" || { echo 'webOS native SDK compiler not found: $(CROSS)gcc; download the webosbrew native-toolchain and set SDK=/path/to/arm-webos-linux-gnueabi_sdk-buildroot (or CROSS=...)' >&2; exit 1; }
	@test -d "$(SYSROOT)/usr/include" || { echo 'webOS native SDK sysroot not found: $(SYSROOT); set SYSROOT=/path/to/sysroot' >&2; exit 1; }
	mkdir -p $(OUT)
	$(CROSS)gcc --sysroot=$(SYSROOT) $(CFLAGS) $(INC) -o $@ $(SOURCES) $(LIBS) -Wl,-O1,--as-needed
	$(CROSS)strip $@

# Dev build: any luna sender may call it (for luna-send on a test TV). Never ship this one.
dev:
	$(MAKE) OUT=$(OUT)-dev EXTRA_CFLAGS=-DHC_ALLOW_ANY_SENDER

test: test/test-policy
	./test/test-policy

test/test-policy: test/test-policy.c src/policy.c src/policy.h
	$(CC) -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -Isrc -o $@ test/test-policy.c src/policy.c

check-abi: $(OUT)/homechooser-service
	@test -d "$(ROOTFS)" || { echo 'webOS firmware rootfs not found: $(ROOTFS); set ROOTFS=/path/to/rootfs or FIRMWARES=/path/to/unpacked-firmwares' >&2; exit 1; }
	CROSS=$(CROSS) sh test/check-abi.sh $(OUT)/homechooser-service $(ROOTFS)

clean:
	rm -rf $(OUT) $(OUT)-dev test/test-policy

.PHONY: all dev test test-homekey check-abi clean

# Run the same native JSON/transaction code under qemu with a fake Luna transport.
test-homekey: $(OUT)/test-homekey
	@mkdir -p $(OUT)/keyfilter-test
	qemu-arm -L $(KEYFILTER_ROOTFS) $(OUT)/test-homekey $(OUT)/keyfilter-test $(KEYFILTER_ROOTFS)/usr/lib/qml/KeyFilters/systemUi.js

$(OUT)/test-homekey: test/test-homekey.c src/homekey.c src/homekey-generator.c src/homekey.h src/homekey-shapes.inc
	mkdir -p $(OUT)
	$(CROSS)gcc --sysroot=$(SYSROOT) $(CFLAGS) $(INC) -Isrc -o $@ test/test-homekey.c src/homekey.c src/homekey-generator.c -lpbnjson_c -lglib-2.0
