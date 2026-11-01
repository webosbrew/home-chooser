# SPDX-License-Identifier: Apache-2.0
# Host-side tests of the native code, run with CTest:
#   cmake -S service/test -B build/service-host && cmake --build build/service-host && ctest --test-dir build/service-host
#   test-policy        the pure policy, built natively with the address and undefined sanitizers
#   test-homekey       native live-filter transactions with fake Luna             } ARM programs from
#   test-watcher       native watcher policy                                       } ../CMakeLists.txt,
#   test-watcher-luna  watcher child transport/runtime, through fake-luna-send.py  } cross built into
#   test-gate          the home gate's decision table and self-release             } build/service-host/arm
#   test-gate-mount    the daemon's gate mount/unmount decisions                    } and run under qemu-arm
#   test-launcher-contract  the real launcher API handlers on a fake hub, against api/launcher-v1.openrpc.json
#                      (ARM program test-launcher-contract, driven by launcher-spec.py)
#   test-launcher-spec the launcher API description against the OpenRPC meta-schema (python3 only)
#   test-launcher-doc  docs/launcher-api.md's methods and errorCode lists against the spec (python3 only)
#   test-launcher-client     api/client is what api/tools/gen-client.py makes from the spec (python3 only)
#   test-launcher-client-js  the client and mock in node against a fake bridge (when node is found)
# The ARM programs run against KEYFILTER_ROOTFS's libraries (and its systemUi.js); the paths are in
# ../cmake/settings.cmake. -DQEMU_TESTS=OFF builds and runs test-policy and the launcher API checks alone,
# without the SDK or a firmware rootfs.
cmake_minimum_required(VERSION 3.20)
project(homechooser-tests LANGUAGES C)
enable_testing()

option(QEMU_TESTS "Cross build the ARM test programs and run them under qemu-arm" ON)
include(../cmake/settings.cmake)
set(src "${CMAKE_CURRENT_SOURCE_DIR}/../src")

add_executable(test-policy test-policy.c "${src}/policy.c")
target_include_directories(test-policy PRIVATE "${src}")
target_compile_options(test-policy PRIVATE -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined)
target_link_options(test-policy PRIVATE -fsanitize=address,undefined)
set(work "${CMAKE_CURRENT_BINARY_DIR}")
# test-policy makes and removes test/iconXXXXXX under its working directory.
add_test(NAME test-policy COMMAND test-policy WORKING_DIRECTORY "${work}/policy-test")
set_tests_properties(test-policy PROPERTIES FIXTURES_REQUIRED scratch)
# Scratch directories the programs write into (kept between runs, as before).
add_test(NAME scratch-dirs COMMAND "${CMAKE_COMMAND}" -E make_directory "${work}/policy-test/test"
	"${work}/keyfilter-test" "${work}/watcher-test" "${work}/gate-test" "${work}/gate-mount-test"
	"${work}/launcher-contract-test")
set_tests_properties(scratch-dirs PROPERTIES FIXTURES_SETUP scratch)

# The launcher API description (api/README.md): checked here, and against the real handlers below.
find_program(PYTHON3 python3 REQUIRED)
set(api "${CMAKE_CURRENT_SOURCE_DIR}/../../api")
set(launcher_spec "${PYTHON3}" -I "${CMAKE_CURRENT_SOURCE_DIR}/launcher-spec.py")
set(launcher_cases "${CMAKE_CURRENT_SOURCE_DIR}/launcher-contract-cases.json")
add_test(NAME test-launcher-spec COMMAND ${launcher_spec} lint "${api}/launcher-v1.openrpc.json" "${launcher_cases}"
	"${api}/vendor/openrpc-meta-schema-1.14.9.json" "${api}/vendor/json-schema-tools-meta-schema-1.8.3.json")
# docs/launcher-api.md names the same methods, per-method errorCode lists and error table as the spec.
add_test(NAME test-launcher-doc COMMAND ${launcher_spec} doc "${api}/launcher-v1.openrpc.json"
	"${CMAKE_CURRENT_SOURCE_DIR}/../../docs/launcher-api.md")
# The generated client (api/client) and its copies in examples/web-launcher match the spec; with node,
# the client and the mock run against a fake bridge and every mock reply validates against the spec.
add_test(NAME test-launcher-client COMMAND "${PYTHON3}" -I "${api}/tools/gen-client.py" --check)
find_program(NODE node)
if(NODE)
	add_test(NAME test-launcher-client-js COMMAND "${PYTHON3}" -I "${api}/tools/test-client.py" "${NODE}")
else()
	message(STATUS "node not found: test-launcher-client-js is not run")
endif()

if(NOT QEMU_TESTS)
	return()
endif()

if(NOT EXISTS "${CROSS}gcc")
	message(FATAL_ERROR "webOS native SDK compiler not found (${CROSS}gcc); set SDK or CROSS, or -DQEMU_TESTS=OFF for test-policy alone")
endif()
find_program(QEMU_ARM qemu-arm)
if(NOT QEMU_ARM)
	message(FATAL_ERROR "qemu-arm not found; install qemu-user, or -DQEMU_TESTS=OFF for test-policy alone")
endif()
if(NOT IS_DIRECTORY "${KEYFILTER_ROOTFS}")
	message(WARNING "firmware rootfs not found: ${KEYFILTER_ROOTFS}; the qemu tests will fail. Set KEYFILTER_ROOTFS=/path/to/rootfs or FIRMWARES=/path/to/unpacked-firmwares")
endif()

# The ARM programs: ../CMakeLists.txt configured with the webOS toolchain in a build directory of its own.
include(ExternalProject)
set(arm "${CMAKE_CURRENT_BINARY_DIR}/arm")
set(programs test-homekey test-watcher test-watcher-luna test-gate test-gate-mount test-launcher-contract)
set(cache_args -DSDK:PATH=${SDK} -DSYSROOT:PATH=${SYSROOT} -DCROSS:STRING=${CROSS})
foreach(name VERSION EXTRA_CFLAGS)
	if(NOT "${${name}}" STREQUAL "")
		list(APPEND cache_args "-D${name}:STRING=${${name}}")
	endif()
endforeach()
list(TRANSFORM programs PREPEND "${arm}/" OUTPUT_VARIABLE byproducts)
ExternalProject_Add(arm-programs
	SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/.."
	BINARY_DIR "${arm}"
	CMAKE_CACHE_ARGS ${cache_args}
	BUILD_COMMAND "${CMAKE_COMMAND}" --build "${arm}" --target ${programs}
	BUILD_ALWAYS ON
	BUILD_BYPRODUCTS ${byproducts}
	INSTALL_COMMAND "")

set(qemu "${QEMU_ARM}" -L "${KEYFILTER_ROOTFS}")
set(system_ui "${KEYFILTER_ROOTFS}/usr/lib/qml/KeyFilters/systemUi.js")
set(fake_luna_send "${CMAKE_CURRENT_SOURCE_DIR}/fake-luna-send.py")

# test-watcher-luna runs from a copy named runtime-image, which it unlinks while running.
add_test(NAME watcher-runtime-image COMMAND "${CMAKE_COMMAND}" -E copy "${arm}/test-watcher-luna" "${work}/watcher-test/runtime-image")
set_tests_properties(watcher-runtime-image PROPERTIES FIXTURES_SETUP runtime-image FIXTURES_REQUIRED scratch)

add_test(NAME test-homekey COMMAND ${qemu} "${arm}/test-homekey" "${work}/keyfilter-test" "${system_ui}")
add_test(NAME test-watcher COMMAND ${qemu} "${arm}/test-watcher" "${work}/watcher-test")
add_test(NAME test-watcher-luna COMMAND ${qemu} "${work}/watcher-test/runtime-image" "${work}/watcher-test" "${system_ui}" "${fake_luna_send}")
add_test(NAME test-gate COMMAND ${qemu} "${arm}/test-gate" "${work}/gate-test" "${fake_luna_send}")
add_test(NAME test-gate-mount COMMAND ${qemu} "${arm}/test-gate-mount" "${work}/gate-mount-test")
# launcher-spec.py writes each case's files under launcher-contract-test/cases and runs the driver as
# qemu-arm -L KEYFILTER_ROOTFS test-launcher-contract.
add_test(NAME test-launcher-contract COMMAND ${launcher_spec} contract "${api}/launcher-v1.openrpc.json" "${launcher_cases}"
	"${arm}/test-launcher-contract" "${work}/launcher-contract-test" ${qemu})
set_tests_properties(test-homekey test-watcher test-gate test-gate-mount test-launcher-contract PROPERTIES FIXTURES_REQUIRED scratch)
set_tests_properties(test-watcher-luna PROPERTIES FIXTURES_REQUIRED "scratch;runtime-image")
# The two watcher programs share watcher-test; never run them at once.
set_tests_properties(test-watcher test-watcher-luna watcher-runtime-image PROPERTIES RESOURCE_LOCK watcher-test)
