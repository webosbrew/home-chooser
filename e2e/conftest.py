# SPDX-License-Identifier: Apache-2.0
"""pytest in e2e/: the offline unit tests (test_*.py, unittest) and the device tests (device/, with --vm)."""
pytest_plugins = ['device.plugin']
