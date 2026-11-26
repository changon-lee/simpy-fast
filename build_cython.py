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

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                      os.path.join(ROOT, 'build', 'cy'))
MODULES = [
    'simpy.core',
    'simpy.events',
    'simpy.resources.base',
    'simpy.resources.container',
    'simpy.resources.resource',
    'simpy.resources.store',
]

pkg_out = os.path.join(OUT, 'simpy')
shutil.rmtree(pkg_out, ignore_errors=True)
shutil.copytree(os.path.join(ROOT, 'src', 'simpy'), pkg_out,
                ignore=shutil.ignore_patterns('__pycache__'))

exts = [
    Extension(m, [os.path.join(OUT, *m.split('.')) + '.py'])
    for m in MODULES
]
ext_modules = cythonize(
    exts,
    language_level=3,
    quiet=True,
    compiler_directives={
        'binding': True,
        # Annotations in the .py files stay documentation only: typing them
        # would make Cython coerce or reject arguments (an `int` priority
        # would truncate 1.5 to 1). All typing lives in the .pxd files.
        'annotation_typing': False,
        'embedsignature': True,
        # CY_PROFILE=1 builds a cProfile-visible variant for diagnosis only.
        'profile': os.environ.get('CY_PROFILE') == '1',
    },
)
dist = Distribution({'ext_modules': ext_modules})
cmd = dist.get_command_obj('build_ext')
cmd.inplace = False
cmd.build_lib = OUT
cmd.build_temp = os.path.join(ROOT, 'build', 'temp')
cmd.ensure_finalized()
cmd.run()
for m in MODULES:
    print('built', m)
