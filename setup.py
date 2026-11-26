"""Build the optimized SimPy as a wheel: pip wheel . --no-build-isolation

With SIMPY_PGO=1 the extension modules are built with profile-guided
optimization (MSVC; see pgo.py): instrumented build, training run of
bench/pgo_train.py, optimized build.

The package keeps the import name ``simpy``. Without a C compiler or Cython,
install src/simpy as plain Python instead; it runs the same code uncompiled.
"""
import os
import shutil

from Cython.Build import cythonize
from setuptools import Extension, find_packages, setup
from setuptools.command.build_ext import build_ext

import pgo
from cython_config import DIRECTIVES, MODULES, patch_generated_c

exts = [
    Extension(m, [os.path.join('src', *m.split('.')) + '.pyx'],
              include_dirs=[os.path.join('src', 'simpy')])
    for m in MODULES
]
# force: patch_generated_c() patches freshly generated C only, never C
# left over from an earlier build.
ext_modules = cythonize(exts, quiet=True, force=True,
                        compiler_directives=DIRECTIVES)
patch_generated_c(ext_modules, os.path.join('src', 'simpy'))


class BuildExt(build_ext):
    def run(self):
        if os.environ.get('SIMPY_PGO') != '1':
            return super().run()
        pgd_dir = os.path.abspath(os.path.join(self.build_temp, 'pgo'))
        shutil.rmtree(pgd_dir, ignore_errors=True)
        os.makedirs(pgd_dir)
        compiler, self.force = self.compiler, True
        for stage in ('instrument', 'optimize'):
            for ext in self.extensions:
                ext.extra_link_args = pgo.link_args(stage, pgd_dir, ext.name)
            self.compiler = compiler
            super().run()
            if stage == 'instrument':
                runtime = pgo.pgort_dir(self.compiler)
                if runtime is None:
                    raise SystemExit('SIMPY_PGO=1 needs MSVC and its '
                                     'profiling runtime (pgort140.dll)')
                tree = 'src' if self.inplace else self.build_lib
                pgo.train(os.path.abspath(tree), runtime, pgd_dir)


setup(
    name='simpy',
    version='4.0.2+fast2',
    description='SimPy 4.0.2 with Cython-compiled core and resources',
    license='MIT',
    package_dir={'': 'src'},
    packages=find_packages('src'),
    package_data={'simpy': ['*.pxd', 'py.typed'], 'simpy.resources': ['*.pxd']},
    ext_modules=ext_modules,
    cmdclass={'build_ext': BuildExt},
    python_requires='>=3.6',
    zip_safe=False,
)
