# Cython declarations for container.py (pure-Python mode).
cimport cython
from simpy.resources cimport base


cdef class ContainerPut(base.Put):
    cdef public object amount


cdef class ContainerGet(base.Get):
    cdef public object amount


cdef class Container(base.BaseResource):
    cdef public object _level
    @cython.locals(ev=ContainerPut)
    cpdef _do_put(self, event)
    @cython.locals(ev=ContainerGet)
    cpdef _do_get(self, event)
