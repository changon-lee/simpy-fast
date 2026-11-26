"""Cython settings shared by setup.py (wheels) and build_cython.py (bench trees)."""
import os

# Modules compiled to extension types. The rest of the package stays Python.
MODULES = [
    'simpy.core',
    'simpy.events',
    'simpy.resources.base',
    'simpy.resources.container',
    'simpy.resources.resource',
    'simpy.resources.store',
]

DIRECTIVES = {
    'language_level': 3,
    'binding': True,
    'embedsignature': False,
    # Annotations in the .py files stay documentation only: typing them would
    # make Cython coerce or reject arguments (an `int` priority would truncate
    # 1.5 to 1). All typing lives in the .pxd files.
    'annotation_typing': False,
    # CY_PROFILE=1 builds a cProfile-visible variant for diagnosis only.
    'profile': os.environ.get('CY_PROFILE') == '1',
}
