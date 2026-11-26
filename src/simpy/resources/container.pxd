# Cython declarations for container.py (pure-Python mode).
from simpy.resources cimport base


cdef class ContainerPut(base.Put):
    cdef public object amount


cdef class ContainerGet(base.Get):
    cdef public object amount


cdef class Container(base.BaseResource):
    cdef public object _level
    cpdef _do_put(self, event)
    cpdef _do_get(self, event)
