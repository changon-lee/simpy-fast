# Cython declarations for resource.pyx.
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
    cdef public object users
    cdef public object queue
    cpdef _do_put(self, event)
    cpdef _do_get(self, event)


cdef class PriorityResource(Resource):
    pass


cdef class PreemptiveResource(PriorityResource):
    cpdef _do_put(self, event)
