/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_VERSION_H
#define HC_VERSION_H
/* From app/package.json, the one source of the version (service/CMakeLists.txt). */
#ifndef HC_VERSION
#error "HC_VERSION is set by service/CMakeLists.txt from app/package.json"
#endif
#endif
