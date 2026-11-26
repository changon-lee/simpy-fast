"""Training run for a profile-guided build (see pgo.py).

Usage: python bench/pgo_train.py TREE
Runs every workload of workloads.py and coldpaths.py once against the
instrumented SimPy in TREE. SIMPY_PGORT_DIR names the directory of the
compiler's profiling runtime, which the instrumented modules load.
"""
import os
import sys
import tempfile
from importlib import metadata

tree = os.path.abspath(sys.argv[1])
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [tree, here]
if os.environ.get('SIMPY_PGORT_DIR'):
    os.add_dll_directory(os.environ['SIMPY_PGORT_DIR'])
try:
    metadata.version('simpy')
except metadata.PackageNotFoundError:
    # simpy/__init__.py reads its version from the installed distribution;
    # a wheel build may run before any is installed.
    info = os.path.join(tempfile.mkdtemp(), 'simpy-0.dist-info')
    os.makedirs(info)
    with open(os.path.join(info, 'METADATA'), 'w') as f:
        f.write('Metadata-Version: 2.1\nName: simpy\nVersion: 0\n')
    sys.path.append(os.path.dirname(info))

import simpy  # noqa: E402
import coldpaths  # noqa: E402
import workloads  # noqa: E402

assert os.path.abspath(simpy.__file__).startswith(tree), simpy.__file__
for workload in list(workloads.WORKLOADS.values()) + coldpaths.WORKLOADS:
    workload(simpy)
print('trained')
