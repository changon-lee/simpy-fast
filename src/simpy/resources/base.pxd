# Cython declarations for base.py (pure-Python mode). Ignored when the
# module runs uncompiled.
cimport cython
from simpy.core cimport Environment
from simpy.events cimport Event

cdef class BaseResource


cdef class Put(Event):
    cdef public BaseResource resource
    cdef public object proc


cdef class Get(Event):
    cdef public BaseResource resource
    cdef public object proc


cdef class BaseResource:
    cdef dict __dict__
    cdef object __weakref__
    cdef public Environment _env
    cdef public object _capacity
    cdef public object put_queue
    cdef public object get_queue
    cpdef _do_put(self, event)
    @cython.locals(put_event=Event)
    cpdef _trigger_put(self, get_event)
    cpdef _do_get(self, event)
    @cython.locals(get_event=Event)
    cpdef _trigger_get(self, put_event)
