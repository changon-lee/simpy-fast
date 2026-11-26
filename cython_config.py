"""Cython settings shared by setup.py (wheels) and build_cython.py (bench trees)."""
import glob
import os
import re

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

# The build patches two spots of the C that Cython generates. Each patch is
# count-checked, so a Cython version that generates different code fails the
# build instead of silently losing the patch.
#
# 1. Cython gives every instance of a class with `cdef dict __dict__` a new
#    dict in tp_new. A NULL slot is valid: CPython's attribute machinery and
#    the __dict__ getter create the dict on first use, and the generated
#    dealloc, traverse and clear functions accept NULL. Events rarely get
#    attributes beyond their fields, so the dict is usually never created.
_EAGER_DICT = b'p->__dict__ = PyDict_New(); if (unlikely(!p->__dict__)) goto bad;'
_LAZY_DICT = b'p->__dict__ = NULL; /* created on first use */'

# 2. When C code calls a cpdef method, Cython checks for a Python override by
#    looking up getattr(self, name), which allocates a bound method, on every
#    call. simpy_cpdef_plain() (_cfast.h) proves the common case without the
#    lookup; when it cannot, Cython's check runs as generated.
_DISPATCH = re.compile(
    rb'if \(unlikely\(__pyx_skip_dispatch\)\) ;(\r?\n'
    rb'(?:(?!__pyx_skip_dispatch).){0,2000}?'
    rb'__Pyx_PyObject_GetAttrStr\(\(\(PyObject \*\)__pyx_v_self\), '
    rb'(__pyx_mstate_global->__pyx_n_\w+)\)'
    rb'(?:(?!__pyx_skip_dispatch).){0,500}?'
    rb'__Pyx_IsSameCFunction\(__pyx_t_\d+, \(void\(\*\)\(void\)\) '
    rb'(__pyx_pw_\w+)\))', re.S)
_CHEAP_DISPATCH = (rb'if (unlikely(__pyx_skip_dispatch) || simpy_cpdef_plain('
                   rb'(PyObject *)__pyx_v_self, \2, (void(*)(void)) \3, '
                   rb'__pyx_mstate_global->__pyx_CyFunctionType)) ;\1')
_CHEAP_MARK = b'|| simpy_cpdef_plain('
_CPDEF = re.compile(r'^\s*cpdef\s+\w+\s*\(', re.M)


def patch_generated_c(ext_modules, pkg_root):
    """Apply the patches above to the C generated for *ext_modules* from the
    package sources under *pkg_root*."""
    declared = 0
    for pxd in glob.glob(os.path.join(pkg_root, '**', '*.pxd'), recursive=True):
        with open(pxd, encoding='utf-8') as f:
            declared += sum(line.split() == ['cdef', 'dict', '__dict__']
                            for line in f)
    lazy = 0
    for ext in ext_modules:
        for src in ext.sources:
            if not src.endswith('.c'):
                continue
            with open(src[:-2] + '.pyx', encoding='utf-8') as f:
                cpdefs = len(_CPDEF.findall(f.read()))
            with open(src, 'rb') as f:
                code = f.read()
            new = _DISPATCH.sub(_CHEAP_DISPATCH, code)
            new = new.replace(_EAGER_DICT, _LAZY_DICT)
            if new != code:
                with open(src, 'wb') as f:
                    f.write(new)
            lazy += new.count(_LAZY_DICT)
            cheap = new.count(_CHEAP_MARK)
            if cheap != cpdefs or b'__pyx_skip_dispatch)) ;' in new:
                raise RuntimeError(f'{src}: patched {cheap} cpdef override '
                                   f'checks, expected {cpdefs}')
    if lazy != declared:
        raise RuntimeError(f'patched {lazy} instance dicts, expected '
                           f'{declared}')
