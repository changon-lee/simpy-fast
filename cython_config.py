"""Cython settings shared by setup.py (wheels) and build_cython.py (bench trees)."""
import bisect
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

# The build patches four spots of the C that Cython generates. Each patch is
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
#    call. simpy_cpdef_plain_cached() (_cfast.h) proves the common case
#    without the lookup; when it cannot, Cython's check runs as generated.
#    Methods of resources pass the attribute cache of the resource, reached
#    through the __pyx_base members of the generated structs, so the C
#    compiler checks the path.
_DISPATCH = re.compile(
    rb'if \(unlikely\(__pyx_skip_dispatch\)\) ;(\r?\n'
    rb'(?:(?!__pyx_skip_dispatch).){0,2000}?'
    rb'__Pyx_PyObject_GetAttrStr\(\(\(PyObject \*\)__pyx_v_self\), '
    rb'(__pyx_mstate_global->__pyx_n_\w+)\)'
    rb'(?:(?!__pyx_skip_dispatch).){0,500}?'
    rb'__Pyx_IsSameCFunction\(__pyx_t_\d+, \(void\(\*\)\(void\)\) '
    rb'(__pyx_pw_\w+)\))', re.S)
_CHEAP_MARK = b'|| simpy_cpdef_plain_cached('
_STRUCT_BASE = re.compile(
    rb'^struct (__pyx_obj_\w+) \{\r?\n  struct (__pyx_obj_\w+) __pyx_base;',
    re.M)
_SELF_STRUCT = re.compile(
    rb'^static PyObject \*__pyx_f_\w+\(struct (__pyx_obj_\w+) \*__pyx_v_self,',
    re.M)
_CACHE_OWNER = b'__pyx_obj_5simpy_9resources_4base_BaseResource'


def _cheap_dispatch(code):
    """Replace each cpdef override check in *code*; return the new code and
    the number of checks that use a resource's attribute cache."""
    parents = dict(_STRUCT_BASE.findall(code))
    funcs = [(m.start(), m.group(1)) for m in _SELF_STRUCT.finditer(code)]
    starts = [pos for pos, _ in funcs]
    cached = 0

    def replace(m):
        nonlocal cached
        # The struct of self in the cpdef function that holds this check.
        struct = funcs[bisect.bisect(starts, m.start()) - 1][1]
        path = b''
        while struct != _CACHE_OWNER and struct in parents:
            struct = parents[struct]
            path += b'__pyx_base.'
        cache = b'NULL'
        if struct == _CACHE_OWNER:
            cache = b'&__pyx_v_self->' + path + b'_acache'
            cached += 1
        return (b'if (unlikely(__pyx_skip_dispatch) || '
                b'simpy_cpdef_plain_cached((PyObject *)__pyx_v_self, ' +
                m.group(2) + b', (void(*)(void)) ' + m.group(3) +
                b', __pyx_mstate_global->__pyx_CyFunctionType, ' + cache +
                b')) ;' + m.group(1))

    return _DISPATCH.sub(replace, code), cached

# 3. Cython's __dict__ attribute has a getter only, so `obj.__dict__ = d` and
#    `del obj.__dict__` raise AttributeError. Instances of Python classes, as
#    upstream's events, environments and resources are, accept both;
#    simpy_set_dict() (_cfast.h) is their setter.
_DICT_GETSET = re.compile(
    rb'\{"__dict__", (__pyx___dict__getter_\w+), 0, 0, 0\}')
_DICT_GETSET_NEW = rb'{"__dict__", \1, simpy_set_dict, 0, 0}'
_DICT_GETTER = re.compile(rb'^static PyObject \*__pyx___dict__getter_\w+\(',
                          re.M)

# 4. The attribute cache of a resource (SimpyAttrCache, _cfast.h) is a member
#    of the BaseResource struct that Cython does not know of: as a cdef field
#    it would end Cython's pickle support, which copy.copy() of a resource
#    uses. Every module that defines the struct gets the member; Cython's
#    size check at import fails if the modules disagree.
_RESOURCE_STRUCT = re.compile(
    rb'(struct ' + _CACHE_OWNER + rb' \{(\r?\n)(?:  [^\r\n]*\r?\n)*)(\};)')
_RESOURCE_STRUCT_NEW = rb'\1  SimpyAttrCache _acache;\2\3'
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
            new, cached = _cheap_dispatch(code)
            new = new.replace(_EAGER_DICT, _LAZY_DICT)
            new, setters = _DICT_GETSET.subn(_DICT_GETSET_NEW, new)
            getters = len(_DICT_GETTER.findall(new))
            if setters != getters:
                raise RuntimeError(f'{src}: patched {setters} __dict__ '
                                   f'setters, expected {getters}')
            new, members = _RESOURCE_STRUCT.subn(_RESOURCE_STRUCT_NEW, new)
            resource_module = ext.name.startswith('simpy.resources.')
            if members != resource_module:
                raise RuntimeError(f'{src}: {members} BaseResource structs '
                                   f'patched, expected {int(resource_module)}')
            if new != code:
                with open(src, 'wb') as f:
                    f.write(new)
            lazy += new.count(_LAZY_DICT)
            cheap = new.count(_CHEAP_MARK)
            if cheap != cpdefs or b'__pyx_skip_dispatch)) ;' in new:
                raise RuntimeError(f'{src}: patched {cheap} cpdef override '
                                   f'checks, expected {cpdefs}')
            # All cpdef methods of simpy.resources belong to resources.
            if cached != (cpdefs if resource_module else 0):
                raise RuntimeError(f'{src}: {cached} of {cpdefs} cpdef '
                                   f'checks use the attribute cache')
    if lazy != declared:
        raise RuntimeError(f'patched {lazy} instance dicts, expected '
                           f'{declared}')
