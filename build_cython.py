"""Compile the hot SimPy modules in src/simpy with Cython.

Usage: python build_cython.py [--pgo] [OUT_DIR]

Copies src/simpy to OUT_DIR/simpy (default build/cy) and builds extension
modules next to the .py files, so OUT_DIR can be put on sys.path. The .py
sources stay importable as the fallback when no compiled module matches.
--pgo builds with profile-guided optimization (MSVC, see pgo.py).
"""
import os
import shutil
import sys

from Cython.Build import cythonize
from setuptools import Extension
from setuptools.dist import Distribution

import pgo
from cython_config import DIRECTIVES, MODULES, patch_generated_c

ROOT = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
use_pgo = '--pgo' in args
args = [a for a in args if a != '--pgo']
OUT = os.path.abspath(args[0] if args else os.path.join(ROOT, 'build', 'cy'))
pkg_out = os.path.join(OUT, 'simpy')
pgd_dir = os.path.join(OUT, '_pgo')
shutil.rmtree(pkg_out, ignore_errors=True)
shutil.copytree(os.path.join(ROOT, 'src', 'simpy'), pkg_out,
                ignore=shutil.ignore_patterns('__pycache__'))

exts = [
    Extension(m, [os.path.join(OUT, *m.split('.')) + '.pyx'],
              include_dirs=[pkg_out])
    for m in MODULES
]
ext_modules = cythonize(exts, quiet=True, compiler_directives=DIRECTIVES)
patch_generated_c(ext_modules, pkg_out)


def build(stage=None):
    if stage:
        for ext in ext_modules:
            ext.extra_link_args = pgo.link_args(stage, pgd_dir, ext.name)
    dist = Distribution({'ext_modules': ext_modules})
    cmd = dist.get_command_obj('build_ext')
    cmd.inplace = False
    cmd.force = True
    cmd.build_lib = OUT
    cmd.build_temp = os.path.join(ROOT, 'build', 'temp')
    cmd.ensure_finalized()
    cmd.run()
    return cmd


if use_pgo:
    shutil.rmtree(pgd_dir, ignore_errors=True)
    os.makedirs(pgd_dir)
    runtime = pgo.pgort_dir(build('instrument').compiler)
    if runtime is None:
        sys.exit('--pgo needs MSVC and its profiling runtime (pgort140.dll)')
    pgo.train(OUT, runtime, pgd_dir)
    build('optimize')
else:
    build()
for m in MODULES:
    print('built', m)
