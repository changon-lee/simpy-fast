"""Cython settings shared by setup.py (wheels) and build_cython.py (bench trees)."""
import glob
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

# Cython gives every instance of a class with `cdef dict __dict__` a new dict
# in tp_new. A NULL slot is valid: CPython's attribute machinery and the
# __dict__ getter create the dict on first use, and the generated dealloc,
# traverse and clear functions accept NULL. Events rarely get attributes
# beyond their fields, so the dict is usually never created.
_EAGER_DICT = b'p->__dict__ = PyDict_New(); if (unlikely(!p->__dict__)) goto bad;'
_LAZY_DICT = b'p->__dict__ = NULL; /* created on first use */'


def lazy_instance_dicts(ext_modules, pxd_root):
    """Patch the C generated for *ext_modules* to create instance dicts on
    first use. Fails if the number of patched classes differs from the
    number of `cdef dict __dict__` declarations under *pxd_root*."""
    declared = 0
    for pxd in glob.glob(os.path.join(pxd_root, '**', '*.pxd'), recursive=True):
        with open(pxd, encoding='utf-8') as f:
            declared += sum(line.split() == ['cdef', 'dict', '__dict__']
                            for line in f)
    patched = 0
    for ext in ext_modules:
        for src in ext.sources:
            if not src.endswith('.c'):
                continue
            with open(src, 'rb') as f:
                code = f.read()
            if _EAGER_DICT in code:
                code = code.replace(_EAGER_DICT, _LAZY_DICT)
                with open(src, 'wb') as f:
                    f.write(code)
            patched += code.count(_LAZY_DICT)
    if patched != declared:
        raise RuntimeError(f'lazy_instance_dicts: patched {patched} classes, '
                           f'expected {declared}')
