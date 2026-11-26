# Cython declarations for resource.py (pure-Python mode).
cimport cython
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


cdef class Resource(base.BaseResource):
    cdef public list users
    cdef public object queue
    @cython.locals(ev=Request)
    cpdef _do_put(self, event)
    @cython.locals(ev=Release)
    cpdef _do_get(self, event)


cdef class PriorityResource(Resource):
    pass


cdef class PreemptiveResource(PriorityResource):
    cpdef _do_put(self, event)
