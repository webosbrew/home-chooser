# SPDX-License-Identifier: Apache-2.0
"""The device tests of one VM run: test_<vm>[<launcher>-<phase>], one per phase of the run's plan.

    pytest e2e/device -v --vm 9.x --tier smoke          # see e2e/README.md "Quick start"

Without --vm or --tier nothing is defined here, so `pytest e2e` runs the offline unit tests only.
"""
from device import plugin

plugin.define_tests(globals())
