#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end test of Home Chooser on a Beanjar VM or an author's TV.  See e2e/README.md.

The command line only: the runner is the hc_e2e package next to this file (Python 3 standard library
only; e2e/README.md "Code layout").  The options, the output files (run.log, results.json, the
screenshots) and the exit status are those of hc_e2e.cli.main().
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hc_e2e.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
