# Cython declarations for base.pyx.
cimport cython
from simpy.core cimport Environment
from simpy.events cimport Event

cdef class BaseResource


cdef class Put(Event):
    cdef public object resource
    cdef public object proc


cdef class Get(Event):
    cdef public object resource
    cdef public object proc


cdef int put_init(Put self, object resource) except -1
cdef int get_init(Get self, object resource) except -1
cdef int put_exit(Put self) except -1


cdef class BaseResource:
    cdef dict __dict__
    cdef object __weakref__
    cdef public object _env
    cdef public object _capacity
    cdef public object put_queue
    cdef public object get_queue
    cpdef _do_put(self, event)
    cpdef _trigger_put(self, get_event)
    cpdef _do_get(self, event)
    cpdef _trigger_get(self, put_event)
