# SPDX-License-Identifier: Apache-2.0
# Cross build for webOS (ARM): the service, the daemon and the home gate.
#   cmake -S service -B build/service && cmake --build build/service
#   cmake --build build/service --target check-abi    GLIBC/GLIBCXX versions and SONAMEs, against ROOTFS
# Dev build, where any luna sender may call the service (for luna-send on a test TV; never ship it):
#   cmake -S service -B build/service-dev -DEXTRA_CFLAGS=-DHC_ALLOW_ANY_SENDER
# The host-side tests are a separate project, service/test (see its CMakeLists.txt).
#
# The build machine's paths (SDK, SYSROOT, CROSS, FIRMWARES, ROOTFS, KEYFILTER_ROOTFS) are in
# cmake/settings.cmake. VERSION, the version compiled in, is app/package.json's, the one source of
# the version, unless -DVERSION or the environment sets it. CFLAGS defaults to -O2 -g0; -DCMAKE_C_FLAGS
# or the environment's CFLAGS replaces it. EXTRA_CFLAGS adds flags to every compile.
cmake_minimum_required(VERSION 3.20)

if(NOT DEFINED CMAKE_TOOLCHAIN_FILE)
	set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/cmake/webos-toolchain.cmake")
endif()
if(NOT DEFINED CMAKE_C_FLAGS AND "$ENV{CFLAGS}" STREQUAL "")
	set(CMAKE_C_FLAGS "-O2 -g0" CACHE STRING "Flags for every C compile and link")
endif()

project(homechooser LANGUAGES C)

include(cmake/settings.cmake)

set(package_json "${CMAKE_CURRENT_SOURCE_DIR}/../app/package.json")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${package_json}")
if("${VERSION}" STREQUAL "")
	set(VERSION "$ENV{VERSION}")
endif()
if("${VERSION}" STREQUAL "")
	file(STRINGS "${package_json}" version_line REGEX "\"version\": *\"[^\"]*\"" LIMIT_COUNT 1)
	string(REGEX REPLACE ".*\"version\": *\"([^\"]*)\".*" "\\1" VERSION "${version_line}")
endif()
if("${VERSION}" STREQUAL "")
	message(FATAL_ERROR "no version in app/package.json")
endif()
message(STATUS "Home Chooser version ${VERSION}")

add_compile_options(-std=gnu11 -Wall -Wextra -Werror)
# CMake compiles absolute paths; keep __FILE__ relative to service/ (src/x.c), as make had it.
add_compile_options("-fmacro-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}/=")
add_compile_definitions(
	_FILE_OFFSET_BITS=64
	GLIB_VERSION_MIN_REQUIRED=GLIB_VERSION_2_40
	GLIB_VERSION_MAX_ALLOWED=GLIB_VERSION_2_40
	HC_VERSION="${VERSION}")
separate_arguments(extra_cflags UNIX_COMMAND "${EXTRA_CFLAGS}")
add_compile_options(${extra_cflags})
include_directories(
	src
	"${SYSROOT}/usr/include"
	"${SYSROOT}/usr/include/glib-2.0"
	"${SYSROOT}/usr/lib/glib-2.0/include")

set(COMMON_SOURCES src/json.c src/luna.c src/proc.c src/fs.c src/policy.c src/homekey.c src/homekey-generator.c src/gate-mount.c src/last-input.c)
set(SERVICE_SOURCES src/main.c src/apps.c src/bootlink.c src/notify.c
	src/service/main.c src/service/reply.c src/service/files.c src/service/call.c
	src/service/run.c src/service/launcher.c src/service/catalog.c src/service/subscribe.c
	src/service/system.c src/luna-child.c)
set(WATCHER_SOURCES src/watcher/core.c src/watcher/record.c src/watcher/restore.c src/watcher/preload.c src/watcher/notify.c src/watcher/gate.c src/watcher/input.c)
set(DAEMON_SOURCES src/daemon-main.c src/homekey-main.c src/luna-child.c src/watcher/main.c ${WATCHER_SOURCES})
set(GATE_LIB_SOURCES src/gate/decide.c src/gate/run.c)
set(GATE_SOURCES src/gate/main.c ${GATE_LIB_SOURCES})
set(LIBS luna-service2 pbnjson_c PmLogLib glib-2.0)
set(DAEMON_LIBS pbnjson_c PmLogLib glib-2.0)

add_library(homechooser STATIC ${COMMON_SOURCES})

# The shipped binaries, stripped. -Wl,-O1,--as-needed comes after the libraries, as it always has.
function(hc_binary name)
	cmake_parse_arguments(PARSE_ARGV 1 arg "" "" "SOURCES;LIBS")
	add_executable(${name} ${arg_SOURCES})
	target_link_libraries(${name} PRIVATE homechooser ${arg_LIBS} -Wl,-O1,--as-needed)
	add_custom_command(TARGET ${name} POST_BUILD COMMAND "${CMAKE_STRIP}" "$<TARGET_FILE:${name}>" VERBATIM)
endfunction()
hc_binary(homechooser-service SOURCES ${SERVICE_SOURCES} LIBS ${LIBS})
hc_binary(homechooser-daemon SOURCES ${DAEMON_SOURCES} LIBS ${DAEMON_LIBS})
# SAM runs this as com.webos.app.home on 6-9 (docs/home-gate.md); luna-send children only.
hc_binary(homechooser-home-gate SOURCES ${GATE_SOURCES} LIBS ${DAEMON_LIBS})

# ARM test programs, run under qemu by the host test project (service/test); not built by default.
function(hc_test_program name)
	cmake_parse_arguments(PARSE_ARGV 1 arg "" "" "SOURCES;LIBS")
	add_executable(${name} EXCLUDE_FROM_ALL ${arg_SOURCES})
	target_link_libraries(${name} PRIVATE ${arg_LIBS})
endfunction()
# The same native JSON/transaction code with a fake Luna transport.
hc_test_program(test-homekey SOURCES test/test-homekey.c src/homekey.c src/homekey-generator.c LIBS pbnjson_c glib-2.0)
hc_test_program(test-watcher
	SOURCES test/test-watcher.c ${WATCHER_SOURCES} src/json.c src/fs.c src/proc.c src/policy.c src/homekey.c src/homekey-generator.c src/gate-mount.c src/last-input.c
	LIBS pbnjson_c glib-2.0)
hc_test_program(test-watcher-luna
	SOURCES test/test-watcher-luna.c src/luna.c src/luna-child.c src/watcher/main.c ${WATCHER_SOURCES} src/json.c src/fs.c src/proc.c src/policy.c src/homekey.c src/homekey-generator.c src/gate-mount.c src/last-input.c
	LIBS ${DAEMON_LIBS})
# The gate's decision table through real luna-send children (the fake one).
hc_test_program(test-gate SOURCES test/test-gate.c ${GATE_LIB_SOURCES} src/json.c src/proc.c src/policy.c src/fs.c src/gate-mount.c src/last-input.c LIBS ${DAEMON_LIBS})
# The daemon's mount/unmount decisions with the mount syscalls and mountinfo faked.
hc_test_program(test-gate-mount SOURCES test/test-gate-mount.c src/gate-mount.c src/json.c src/fs.c src/policy.c LIBS pbnjson_c glib-2.0)
# The real launcher API handlers on a fake hub (test/fake-ls2.c), for the contract test of
# api/launcher-v1.openrpc.json. Link-time seams only: luna-send runs, the euid, the stored Home choice
# and /dev/urandom are the case's (see test/test-launcher-contract.c).
hc_test_program(test-launcher-contract
	SOURCES test/test-launcher-contract.c test/fake-ls2.c src/service/launcher.c src/service/reply.c
		src/service/catalog.c src/service/subscribe.c src/apps.c src/luna.c src/json.c src/fs.c src/policy.c
	LIBS ${DAEMON_LIBS})
target_link_options(test-launcher-contract PRIVATE
	"LINKER:--wrap=run_argv,--wrap=geteuid,--wrap=read_choice,--wrap=open64,--wrap=hc_fs_prefix")
# The launcher list subscriptions with the real upstream children (luna-child.c, fake luna-send) on
# the fake hub (see test/test-launcher-subscribe.c).
hc_test_program(test-launcher-subscribe
	SOURCES test/test-launcher-subscribe.c test/fake-ls2.c src/service/launcher.c src/service/reply.c
		src/service/catalog.c src/service/subscribe.c src/luna-child.c src/apps.c src/luna.c src/proc.c src/json.c
		src/fs.c src/policy.c
	LIBS ${DAEMON_LIBS})
target_link_options(test-launcher-subscribe PRIVATE "LINKER:--wrap=run_argv,--wrap=geteuid,--wrap=read_choice")

set(check_abi sh "${CMAKE_CURRENT_SOURCE_DIR}/test/check-abi.sh")
set(service "$<TARGET_FILE:homechooser-service>")
set(daemon "$<TARGET_FILE:homechooser-daemon>")
set(gate "$<TARGET_FILE:homechooser-home-gate>")
add_custom_target(check-abi
	COMMAND sh -c "test -d \"$1\" || { echo \"webOS firmware rootfs not found: $1; set ROOTFS=/path/to/rootfs or FIRMWARES=/path/to/unpacked-firmwares\" >&2; exit 1; }" sh "${ROOTFS}"
	COMMAND "${CMAKE_COMMAND}" -E env "CROSS=${CROSS}" ${check_abi} "${service}" "${ROOTFS}"
	COMMAND "${CMAKE_COMMAND}" -E env "CROSS=${CROSS}" ${check_abi} "${daemon}" "${ROOTFS}"
	COMMAND sh -c "! \"$1readelf\" -d \"$2\" | grep -q libluna-service2" sh "${CROSS}" "${daemon}"
	COMMAND sh -c "! \"$1nm\" -D --undefined-only \"$2\" | grep -E ' LS(Register|Call|Gmain)'" sh "${CROSS}" "${daemon}"
	COMMAND "${CMAKE_COMMAND}" -E env "CROSS=${CROSS}" ${check_abi} "${gate}" "${ROOTFS}"
	COMMAND sh -c "! \"$1readelf\" -d \"$2\" | grep -q libluna-service2" sh "${CROSS}" "${gate}"
	VERBATIM)
add_dependencies(check-abi homechooser-service homechooser-daemon homechooser-home-gate)
