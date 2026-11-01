# SPDX-License-Identifier: Apache-2.0
# Toolchain file for the webOS buildroot SDK (arm-webos-linux-gnueabi). SDK, SYSROOT and CROSS are
# described in settings.cmake. service/CMakeLists.txt uses this file unless another is given.
include("${CMAKE_CURRENT_LIST_DIR}/settings.cmake")

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)

if(NOT EXISTS "${CROSS}gcc")
	message(FATAL_ERROR "webOS native SDK compiler not found (${CROSS}gcc); set SDK or CROSS")
endif()
if(NOT IS_DIRECTORY "${SYSROOT}/usr/include")
	message(FATAL_ERROR "webOS native SDK sysroot not found (${SYSROOT}); set SYSROOT")
endif()

set(CMAKE_C_COMPILER "${CROSS}gcc")
set(CMAKE_SYSROOT "${SYSROOT}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
# try_compile re-reads this file in a fresh project; hand it the same paths.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES SDK SYSROOT CROSS)
