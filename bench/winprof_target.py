"""Profiling target for bench/winprof.py.

Usage: python bench/winprof_target.py TREE REPS [workload ...]
Imports SimPy from TREE, prints READY, waits for a line, runs the workloads
REPS times, prints DONE and waits for a line before exiting.
"""
import os
import sys

tree = os.path.abspath(sys.argv[1])
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [tree, here]
# Embeddable Pythons ignore PYTHONPATH; WINPROF_EXTRA adds paths explicitly
# (e.g. a site-packages that holds the simpy dist-info for __version__).
sys.path.extend(p for p in os.environ.get('WINPROF_EXTRA', '').split(';') if p)

import simpy  # noqa: E402
from workloads import WORKLOADS  # noqa: E402

assert os.path.abspath(simpy.__file__).startswith(tree), simpy.__file__
names = sys.argv[3:] or list(WORKLOADS)
print('READY', flush=True)
sys.stdin.readline()
for _ in range(int(sys.argv[2])):
    for name in names:
        WORKLOADS[name](simpy)
print('DONE', flush=True)
sys.stdin.readline()
