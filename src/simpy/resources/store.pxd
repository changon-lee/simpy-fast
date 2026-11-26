# Cython declarations for store.py (pure-Python mode).
from simpy.resources cimport base


cdef class StorePut(base.Put):
    cdef public object item


cdef class StoreGet(base.Get):
    pass


cdef class FilterStoreGet(StoreGet):
    cdef public object filter
