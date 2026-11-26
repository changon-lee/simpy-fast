# Cython declarations for store.py (pure-Python mode).
from simpy.resources cimport base


cdef class StorePut(base.Put):
    cdef public object item


cdef class StoreGet(base.Get):
    pass


cdef class FilterStoreGet(StoreGet):
    cdef public object filter


cdef class Store(base.BaseResource):
    cdef public list items
    cpdef _do_put(self, event)
    cpdef _do_get(self, event)


cdef class PriorityStore(Store):
    cpdef _do_put(self, event)
    cpdef _do_get(self, event)


cdef class FilterStore(Store):
    cpdef _do_get(self, event)
