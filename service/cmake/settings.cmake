# SPDX-License-Identifier: Apache-2.0
# The build machine's paths. Each one is, in order: -D<NAME>=... on the cmake command line, else the
# environment variable of the same name, else the default below. Defaults that name another setting
# follow it (-DSDK=/x moves SYSROOT and CROSS too) unless that one is set as well.
#   SDK        the webOS buildroot SDK (arm-webos-linux-gnueabi)
#   SYSROOT    its sysroot; CROSS the tool prefix (gcc, ar, strip, readelf, nm)
#   FIRMWARES  a directory of unpacked firmware images (else OPENHOUSE_FIRMWARES, as the Home key hook
#              tests read it); ROOTFS the rootfs check-abi reads (5.6.1); KEYFILTER_ROOTFS the 6-9
#              rootfs whose libraries and systemUi.js the qemu tests run against (7.x)

macro(hc_setting name default)
	if("${${name}}" STREQUAL "")
		if(NOT "$ENV{${name}}" STREQUAL "")
			set(${name} "$ENV{${name}}")
		else()
			set(${name} "${default}")
		endif()
	endif()
endmacro()

hc_setting(SDK /opt/arm-webos-linux-gnueabi_sdk-buildroot)
hc_setting(SYSROOT "${SDK}/arm-webos-linux-gnueabi/sysroot")
hc_setting(CROSS "${SDK}/bin/arm-webos-linux-gnueabi-")

if(NOT "$ENV{OPENHOUSE_FIRMWARES}" STREQUAL "")
	hc_setting(FIRMWARES "$ENV{OPENHOUSE_FIRMWARES}")
else()
	hc_setting(FIRMWARES "$ENV{HOME}/Projects/webos-firmwares")
endif()
hc_setting(ROOTFS "${FIRMWARES}/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs")
hc_setting(KEYFILTER_ROOTFS "${FIRMWARES}/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs")
