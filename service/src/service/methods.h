/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_SERVICE_METHODS_H
#define HC_SERVICE_METHODS_H
#include <luna-service2/lunaservice.h>

bool on_call(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_read_file(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_write_file(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_remove_file(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_read_icon(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_link(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_unlink(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_link_status(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_run(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_get_info(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_list_apps(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_list_launch_points(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_list_inputs(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_get_icons(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_running(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_close_by_id(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_uninstall(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_get_foreground(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_get_app_info(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_launcher_get_app_icon(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_uninstall_confirmed(LSHandle *sh, LSMessage *msg, void *ctx);
bool on_reboot_prompt(LSHandle *sh, LSMessage *msg, void *ctx);
#endif
