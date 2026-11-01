# SPDX-License-Identifier: Apache-2.0
"""The host's timing slowdown and what it scales: seconds(), sleep(), and run(), a local process whose
timeout is scaled.

Every other module reaches these as timing.X (never a from-import), so SLOWDOWN set once by the command
line reaches every wait, and a test patches each of them in one place.
"""
import subprocess
import time

SLOWDOWN = 1.0


def seconds(value):
    return value * SLOWDOWN


def sleep(value):
    time.sleep(seconds(value))


def run(argv, timeout=120, input=None, env=None):
    """(rc, stdout+stderr) of a local process."""
    try:
        # no input: stdin is /dev/null, never the runner's own (beanjar ares-shell passes stdin through)
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=seconds(timeout),
                           input=input, env=env, stdin=None if input is not None else subprocess.DEVNULL)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b'').decode('utf-8', 'replace') + '\n[timeout]'
