"""Compile the hot SimPy modules in src/simpy with Cython.

Usage: python build_cython.py [OUT_DIR]

Copies src/simpy to OUT_DIR/simpy (default build/cy) and builds extension
modules next to the .py files, so OUT_DIR can be put on sys.path. The .py
sources stay importable as the fallback when no compiled module matches.
"""
import os
import shutil
import sys

from Cython.Build import cythonize
from setuptools import Extension
from setuptools.dist import Distribution

from cython_config import DIRECTIVES, MODULES

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                      os.path.join(ROOT, 'build', 'cy'))
pkg_out = os.path.join(OUT, 'simpy')
shutil.rmtree(pkg_out, ignore_errors=True)
shutil.copytree(os.path.join(ROOT, 'src', 'simpy'), pkg_out,
                ignore=shutil.ignore_patterns('__pycache__'))

exts = [
    Extension(m, [os.path.join(OUT, *m.split('.')) + '.pyx'],
              include_dirs=[pkg_out])
    for m in MODULES
]
ext_modules = cythonize(exts, quiet=True, compiler_directives=DIRECTIVES)
dist = Distribution({'ext_modules': ext_modules})
cmd = dist.get_command_obj('build_ext')
cmd.inplace = False
cmd.build_lib = OUT
cmd.build_temp = os.path.join(ROOT, 'build', 'temp')
cmd.ensure_finalized()
cmd.run()
for m in MODULES:
    print('built', m)
