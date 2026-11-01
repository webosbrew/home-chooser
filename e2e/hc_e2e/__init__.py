# SPDX-License-Identifier: Apache-2.0
"""Home Chooser end-to-end test on a Beanjar VM or an author's TV (e2e/README.md).

Python 3 standard library only.  The device is reached with the ares-* tools only, all built by the
Device class (hc_e2e.device): a VM through `beanjar ares-TOOL [--root] -d [HOST/]NAME`, an author's TV
(--device) by its ares device name.  The VM host (Beanjar) is driven through BeanjarHost
(hc_e2e.beanjar), locally or over `ssh raspberrypi` (the Pi is a Linux host, not a TV: ares-shell is for
televisions only).  The command line is e2e/homechooser_e2e.py (hc_e2e.cli); the module map is in
e2e/README.md "Code layout".
"""
