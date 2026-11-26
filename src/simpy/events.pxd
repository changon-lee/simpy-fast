# Cython declarations for events.pyx.
cimport cython
from simpy.core cimport Environment

cdef class Event

# C implementation of a method usable as event callback: impl(owner, event).
ctypedef int (*simpy_cb_impl)(object owner, Event event) except -1

cdef class Event:
    cdef dict __dict__
    cdef object __weakref__
    cdef public Environment env
    cdef public list callbacks
    cdef public object _value
    cdef object _ok
    cpdef succeed(self, value=*)

cdef class Timeout(Event):
    cdef public object _delay

cdef class Initialize(Event):
    pass

cdef class Process(Event):
    cdef public object _generator
    cdef public object _target
    cdef object _resume_cb
    @cython.final
    cdef object _resume_bound(self)
    @cython.final
    cdef int _do_resume(self, Event event) except -1

cdef class Condition(Event):
    cdef public object _evaluate
    cdef public tuple _events
    cdef public object _count

cdef int register_callback(object func, type owner,
                           simpy_cb_impl impl) except -1
cdef int register_state_fields(type cls, tuple names) except -1
cdef int run_callbacks(Event event, list callbacks) except -1
