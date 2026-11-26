# Cython declarations for base.py (pure-Python mode). Ignored when the
# module runs uncompiled.
from simpy.core cimport Environment
from simpy.events cimport Event


cdef class Put(Event):
    cdef public object resource
    cdef public object proc


cdef class Get(Event):
    cdef public object resource
    cdef public object proc
