# Cython declarations for core.pyx.
cimport cython
from simpy.events cimport Event

cdef class Environment:
    cdef dict __dict__
    cdef object __weakref__
    cdef public object _now
    cdef public list _queue
    cdef public object _eid
    cdef public object _active_proc

    cpdef _run_inline(self)
