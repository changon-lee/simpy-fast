# Cython declarations for resource.py (pure-Python mode).
from simpy.resources cimport base


cdef class Request(base.Put):
    cdef public object usage_since


cdef class Release(base.Get):
    cdef public object request


cdef class PriorityRequest(Request):
    cdef public object priority
    cdef public object preempt
    cdef public object time
    cdef public object key
