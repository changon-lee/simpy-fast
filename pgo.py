"""Profile-guided optimization (MSVC) for the compiled modules.

build_cython.py --pgo and setup.py (with SIMPY_PGO=1) build in three steps:
instrumented modules, a training run of bench/pgo_train.py against them, and
the final modules optimized with the recorded profile. PGO changes how the C
compiler optimizes (inlining, code layout, branch order), not what the code
does. Other compilers build without it.
"""
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def pgort_dir(compiler):
    """Directory of the MSVC profiling runtime (pgort140.dll) that goes with
    *compiler* (a distutils compiler object), or None when the compiler is
    not MSVC or lacks it."""
    if getattr(compiler, 'compiler_type', None) != 'msvc':
        return None
    if not getattr(compiler, 'initialized', True):
        compiler.initialize()
    cc = getattr(compiler, 'cc', None)
    if not cc:
        return None
    # cl.exe lives in bin/Host<arch>/<target>; the runtime for x64 targets
    # is in bin/HostX64/x64.
    target_dir = os.path.dirname(cc)
    target = os.path.basename(target_dir)
    bindir = os.path.dirname(os.path.dirname(target_dir))
    for host in ('HostX64', 'HostX86'):
        d = os.path.join(bindir, host, target)
        if os.path.exists(os.path.join(d, 'pgort140.dll')):
            return d
    return None


def link_args(stage, pgd_dir, ext_name):
    flag = {'instrument': '/GENPROFILE', 'optimize': '/USEPROFILE'}[stage]
    return [f'{flag}:PGD={os.path.join(pgd_dir, ext_name + ".pgd")}']


def train(tree, runtime_dir, pgd_dir):
    """Run the training workloads against the instrumented modules in
    *tree*, then move the recorded counts next to the .pgd files, where the
    optimizing link reads them."""
    # No .pyc files: the tree may be the build directory a wheel is made of.
    env = dict(os.environ, SIMPY_PGORT_DIR=runtime_dir,
               PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable, os.path.join(ROOT, 'bench', 'pgo_train.py'),
                    tree], env=env, check=True)
    pgcs = glob.glob(os.path.join(tree, '**', '*.pgc'), recursive=True)
    if not pgcs:
        raise RuntimeError('PGO training recorded no profile')
    for pgc in pgcs:
        shutil.move(pgc, os.path.join(pgd_dir, os.path.basename(pgc)))
