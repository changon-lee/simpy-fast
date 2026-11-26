"""Run every workload once against the SimPy tree given as argv[1]; print JSON.

Usage: python bench/run_one.py <path-containing-simpy-package> [workload ...]
"""
import hashlib
import json
import os
import sys
import time

tree = os.path.abspath(sys.argv[1])
sys.path.insert(0, tree)
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import simpy  # noqa: E402
from workloads import WORKLOADS  # noqa: E402

loaded_from = os.path.abspath(simpy.__file__)
if not loaded_from.startswith(tree):
    sys.exit(f'simpy loaded from {loaded_from}, expected under {tree}')

names = sys.argv[2:] or list(WORKLOADS)
out = {'simpy_file': loaded_from, 'results': {}}
for name in names:
    t0 = time.perf_counter()
    ops, trace = WORKLOADS[name](simpy)
    dt = time.perf_counter() - t0
    digest = hashlib.blake2b(repr(trace).encode(), digest_size=8).hexdigest()
    out['results'][name] = {
        'sec': dt, 'ops': ops, 'trace_len': len(trace), 'digest': digest,
    }
print(json.dumps(out))
