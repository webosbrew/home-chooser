/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_LOG_H
#define HC_LOG_H
#include <PmLogLib.h>

extern PmLogContext hc_log;

#define LOGI(id, ...) PmLogInfo(hc_log, id, 0, __VA_ARGS__)
#define LOGE(id, ...) PmLogError(hc_log, id, 0, __VA_ARGS__)
#endif
