# Cython declarations for events.py (pure-Python mode). Ignored when the
# module runs uncompiled.
from simpy.core cimport Environment

cdef class Event:
    cdef dict __dict__
    cdef object __weakref__
    cdef public Environment env
    cdef public list callbacks
    cdef public object _value
    cdef public object _ok
    cpdef succeed(self, value=*)

cdef class Timeout(Event):
    cdef public object _delay

cdef class Initialize(Event):
    pass

cdef class Process(Event):
    cdef public object _generator
    cdef public object _target
    cpdef _resume(self, Event event)

cdef class Condition(Event):
    cdef public object _evaluate
    cdef public tuple _events
    cdef public object _count
