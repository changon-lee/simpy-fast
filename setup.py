"""Build the optimized SimPy as a wheel: pip wheel . --no-build-isolation

The package keeps the import name ``simpy``. Without a C compiler or Cython,
install src/simpy as plain Python instead; it runs the same code uncompiled.
"""
import os

from Cython.Build import cythonize
from setuptools import Extension, find_packages, setup

from cython_config import DIRECTIVES, MODULES

exts = [
    Extension(m, [os.path.join('src', *m.split('.')) + '.py'])
    for m in MODULES
]

setup(
    name='simpy',
    version='4.0.2+fast1',
    description='SimPy 4.0.2 with Cython-compiled core and resources',
    license='MIT',
    package_dir={'': 'src'},
    packages=find_packages('src'),
    package_data={'simpy': ['*.pxd', 'py.typed'], 'simpy.resources': ['*.pxd']},
    ext_modules=cythonize(exts, quiet=True, compiler_directives=DIRECTIVES),
    python_requires='>=3.6',
    zip_safe=False,
)
