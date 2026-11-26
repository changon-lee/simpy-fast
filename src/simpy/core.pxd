# Cython declarations for core.pyx.
cimport cython
from simpy.events cimport Event

cdef extern from "_cfast.h":
    ctypedef struct SimpyAttrWatch:
        pass
    ctypedef struct SimpyAttrCache:
        pass

cdef class Environment:
    cdef dict __dict__
    cdef object __weakref__
    cdef SimpyAttrCache _acache
    cdef public object _now
    cdef public object _active_proc
    cdef object _heap
    cdef object _pyqueue
    cdef object _pyeid
    cdef unsigned long long _next_eid
    cdef bint _compat

    @cython.final
    cdef int _to_compat(self) except -1
    @cython.final
    cdef int _push(self, object at, object priority, object event) except -1
    @cython.final
    cdef int _schedule(self, object event, object priority, object delay,
                       int nargs) except -1
    @cython.final
    cdef int _step(self) except -1
    @cython.final
    cdef bint _quiet(self) except -1

cdef object env_now(object env)
cdef SimpyAttrWatch *attr_watch()
cdef object env_active_process(object env)
