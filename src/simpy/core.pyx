"""
Core components for event-discrete simulation environments.

"""
cimport cython
from cpython.object cimport PyObject, PyTypeObject
from cpython.float cimport PyFloat_CheckExact
from cpython.long cimport PyLong_CheckExact
from cpython.ref cimport Py_DECREF

from simpy.events cimport raise_no_ok, run_callbacks

import copyreg as _copyreg
from heapq import heappush, heappop
from itertools import count
from types import MethodType
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from simpy.events import (
    AllOf,
    AnyOf,
    Event,
    EventPriority,
    Process,
    ProcessGenerator,
    Timeout,
    URGENT,
    NORMAL,
)

# Cython binds cimported types at C level only; upstream also has them as
# module attributes.
globals()['Event'] = Event


cdef extern from "_cheap.h":
    ctypedef struct SimpyQEntry:
        PyObject *time
        PyObject *event
    int simpy_heap_ready() except -1
    object simpy_heap_new()
    Py_ssize_t simpy_heap_len(object heap)
    PyObject *simpy_heap_top_time(object heap)
    bint simpy_time_key(object o, double *out)
    bint simpy_prio_key(object o, long long *out)
    int simpy_heap_push(object heap, double t, long long prio,
                        unsigned long long eid, object time,
                        object event) except -1
    void simpy_heap_pop(object heap, SimpyQEntry *out)
    object simpy_heap_to_list(object heap)

cdef extern from "_cfast.h":
    bint simpy_plain(object obj, PyTypeObject *base, object name, object func)
    bint simpy_plain_type(object obj, PyTypeObject *base, object name,
                          object descr)
    object simpy_add_zero(object now, object zero)

simpy_heap_ready()

Infinity: float = float('inf')  #: Convenience alias for infinity

T = TypeVar('T')
T.__module__ = __name__


class BoundClass(Generic[T]):
    """Allows classes to behave like methods.

    The ``__get__()`` descriptor is basically identical to
    ``function.__get__()`` and binds the first argument of the ``cls`` to the
    descriptor instance.

    """

    def __init__(self, cls: Type[T]):
        self.cls = cls

    def __get__(
        self,
        instance: Optional['BoundClass'],
        owner: Optional[Type['BoundClass']] = None,
    ) -> Union[Type[T], MethodType]:
        if instance is None:
            return self.cls
        return MethodType(self.cls, instance)

    @staticmethod
    def bind_early(instance: object) -> None:
        """Bind all :class:`BoundClass` attributes of the *instance's* class
        to the instance itself to increase performance."""
        for name, obj in instance.__class__.__dict__.items():
            if type(obj) is BoundClass:
                bound_class = getattr(instance, name)
                setattr(instance, name, bound_class)


class EmptySchedule(Exception):
    """Thrown by an :class:`Environment` if there are no further events to be
    processed."""


class StopSimulation(Exception):
    """Indicates that the simulation should stop now."""

    @classmethod
    def callback(cls, event: Event) -> None:
        """Used as callback in :meth:`Environment.run()` to stop the simulation
        when the *until* event occurred."""
        if event.ok:
            raise cls(event.value)
        else:
            raise event._value


SimTime = Union[int, float]


@cython.auto_pickle(False)
cdef class Environment:
    """Execution environment for an event-based simulation. The passing of time
    is simulated by stepping from event to event.

    You can provide an *initial_time* for the environment. By default, it
    starts at ``0``.

    This class also provides aliases for common event types, for example
    :attr:`process`, :attr:`timeout` and :attr:`event`.

    """

    # The event queue lives in a C heap (_heap, see _cheap.h) as long as all
    # keys are plain numbers. When a key is not, or Python code reads or
    # assigns ``_queue`` or ``_eid``, the environment switches for good to
    # upstream's representation: a heapq list of (time, priority, eid, event)
    # tuples (_pyqueue) and an itertools.count (_pyeid).

    def __init__(self, initial_time: SimTime = 0):
        self._now = initial_time
        # The list of all currently scheduled events.
        self._heap = simpy_heap_new()
        self._pyqueue = None
        self._pyeid = None
        self._compat = False
        self._next_eid = 0  # Counter for event IDs
        self._active_proc: Optional[Process] = None

        # Bind all BoundClass instances to "self" to improve performance.
        BoundClass.bind_early(self)

    # Copying and pickling see what they see of upstream's Environment: a
    # plain Python object whose instance dict holds all of its state.

    def __getstate__(self):
        state = {'_now': self._now, '_queue': self._queue, '_eid': self._eid,
                 '_active_proc': self._active_proc}
        state.update(vars(self))
        return state

    def __setstate__(self, state):
        for key, value in state.items():
            if key in ('_now', '_queue', '_eid', '_active_proc'):
                setattr(self, key, value)
            else:
                vars(self)[key] = value

    def __reduce_ex__(self, protocol):
        if protocol >= 2 or type(self).__reduce__ is not object.__reduce__:
            return object.__reduce_ex__(self, protocol)
        return _copyreg.__newobj__, (type(self),), self.__getstate__()

    @property
    def now(self) -> SimTime:
        """The current simulation time."""
        return self._now

    @property
    def active_process(self) -> Optional[Process]:
        """The currently active process of the environment."""
        return self._active_proc

    @property
    def _queue(self):
        # The list of all currently scheduled events, as upstream keeps it.
        self._to_compat()
        return self._pyqueue

    @_queue.setter
    def _queue(self, value):
        self._to_compat()
        self._pyqueue = value

    @property
    def _eid(self):
        # Counter for event IDs, as upstream keeps it.
        self._to_compat()
        return self._pyeid

    @_eid.setter
    def _eid(self, value):
        self._to_compat()
        self._pyeid = value

    if TYPE_CHECKING:
        # This block is only evaluated when type checking with, e.g. Mypy.
        # These are the effective types of the methods created with BoundClass
        # magic and are thus a useful reference for SimPy users as well as for
        # static type checking.

        def process(self, generator: ProcessGenerator) -> Process:
            """Create a new :class:`~simpy.events.Process` instance for
            *generator*."""
            return Process(self, generator)

        def timeout(
            self, delay: SimTime = 0, value: Optional[Any] = None
        ) -> Timeout:
            """Return a new :class:`~simpy.events.Timeout` event with a *delay*
            and, optionally, a *value*."""
            return Timeout(self, delay, value)

        def event(self) -> Event:
            """Return a new :class:`~simpy.events.Event` instance.

            Yielding this event suspends a process until another process
            triggers the event.
            """
            return Event(self)

        def all_of(self, events: Iterable[Event]) -> AllOf:
            """Return a :class:`~simpy.events.AllOf` condition for *events*."""
            return AllOf(self, events)

        def any_of(self, events: Iterable[Event]) -> AnyOf:
            """Return a :class:`~simpy.events.AnyOf` condition for *events*."""
            return AnyOf(self, events)

    else:
        process = BoundClass(Process)
        timeout = BoundClass(Timeout)
        event = BoundClass(Event)
        all_of = BoundClass(AllOf)
        any_of = BoundClass(AnyOf)

    def schedule(
        self,
        event: Event,
        priority: EventPriority = NORMAL,
        delay: SimTime = 0,
    ) -> None:
        """Schedule an *event* with a given *priority* and a *delay*."""
        self._push(self._now + delay, priority, event)

    def peek(self) -> SimTime:
        """Get the time of the next scheduled event. Return
        :data:`~simpy.core.Infinity` if there is no further event."""
        if self._compat or self._heap is None:
            try:
                return self._queue[0][0]
            except IndexError:
                return Infinity
        if simpy_heap_len(self._heap) == 0:
            return Infinity
        return <object>simpy_heap_top_time(self._heap)

    def step(self) -> None:
        """Process the next event.

        Raise an :exc:`EmptySchedule` if no further events are available.

        """
        if self._step():
            _raise_empty()

    cdef int _to_compat(self) except -1:
        if self._compat:
            return 0
        if self._heap is None:  # Cleared by the garbage collector.
            self._pyqueue = []
        else:
            self._pyqueue = simpy_heap_to_list(self._heap)
        self._pyeid = count(self._next_eid)
        self._heap = None
        self._compat = True
        return 0

    cdef int _push(self, object at, object priority, object event) except -1:
        # heappush(self._queue, (at, priority, next(self._eid), event))
        cdef double t
        cdef long long prio
        if not self._compat:
            if (self._heap is not None and simpy_time_key(at, &t)
                    and simpy_prio_key(priority, &prio)):
                simpy_heap_push(self._heap, t, prio, self._next_eid, at, event)
                self._next_eid += 1
                return 0
            self._to_compat()
        heappush(self._pyqueue, (at, priority, next(self._pyeid), event))
        return 0

    cdef int _schedule(self, object event, object priority, object delay,
                       int nargs) except -1:
        # self.schedule(event[, priority[, delay]]) with *nargs* arguments, as
        # the event classes call it. An overriding or patched schedule() is
        # called exactly that way; otherwise the event is pushed directly.
        if not simpy_plain(self, <PyTypeObject *>Environment, _S_SCHEDULE,
                           _SCHEDULE):
            if nargs == 1:
                self.schedule(event)
            elif nargs == 2:
                self.schedule(event, priority)
            else:
                self.schedule(event, priority, delay)
            return 0
        if delay is _ZERO:
            at = simpy_add_zero(self._now, _ZERO)
        else:
            at = self._now + delay
        return self._push(at, priority, event)

    cdef int _step(self) except -1:
        # Pop and process the next event. Return 1 if there is none.
        cdef SimpyQEntry entry
        cdef Event ev
        if self._compat or self._heap is None:
            try:
                self._now, _, _, event = heappop(self._queue)
            except IndexError:
                return 1
        else:
            if simpy_heap_len(self._heap) == 0:
                return 1
            simpy_heap_pop(self._heap, &entry)
            self._now = <object>entry.time
            Py_DECREF(<object>entry.time)
            event = <object>entry.event
            Py_DECREF(<object>entry.event)

        # Process callbacks of the event. Set the events callbacks to None
        # immediately to prevent concurrent modifications.
        if isinstance(event, Event):
            ev = <Event>event
            callbacks = ev.callbacks
            ev.callbacks = None
            run_callbacks(ev, callbacks)
            ok = ev._ok
            if ok is None:
                raise_no_ok(ev)
            if ok or hasattr(ev, '_defused'):
                return 0
            # The event has failed and has not been defused. Crash the
            # environment.
            # Create a copy of the failure exception with a new traceback.
            exc = type(ev._value)(*ev._value.args)
            exc.__cause__ = ev._value
            raise exc

        callbacks, event.callbacks = event.callbacks, None
        for callback in callbacks:
            callback(event)

        if not event._ok and not hasattr(event, '_defused'):
            # The event has failed and has not been defused. Crash the
            # environment.
            # Create a copy of the failure exception with a new traceback.
            exc = type(event._value)(*event._value.args)
            exc.__cause__ = event._value
            raise exc
        return 0

    cdef bint _quiet(self) except -1:
        # True when self.schedule(event) for a NORMAL event runs no Python
        # code: schedule() is the plain method and the C heap, whose keys are
        # plain numbers, takes the entry.
        now = self._now
        return (not self._compat and self._heap is not None
                and (PyLong_CheckExact(now) or PyFloat_CheckExact(now))
                and simpy_plain(self, <PyTypeObject *>Environment,
                                _S_SCHEDULE, _SCHEDULE))

    def run(
        self, until: Optional[Union[SimTime, Event]] = None
    ) -> Optional[Any]:
        """Executes :meth:`step()` until the given criterion *until* is met.

        - If it is ``None`` (which is the default), this method will return
          when there are no further events to be processed.

        - If it is an :class:`~simpy.events.Event`, the method will continue
          stepping until this event has been triggered and will return its
          value.  Raises a :exc:`RuntimeError` if there are no further events
          to be processed and the *until* event was not triggered.

        - If it is a number, the method will continue stepping
          until the environment's time reaches *until*.

        """
        if until is not None:
            if not isinstance(until, Event):
                # Assume that *until* is a number if it is not None and
                # not an event.  Create a Timeout(until) in this case.
                at: SimTime
                if isinstance(until, int):
                    at = until
                else:
                    at = float(until)

                if at <= self.now:
                    raise ValueError(
                        f'until(={at}) must be > the current simulation time.'
                    )

                # Schedule the event before all regular timeouts.
                until = Event(self)
                until._ok = True
                until._value = None
                self.schedule(until, URGENT, at - self.now)

            elif until.callbacks is None:
                # Until event has already been processed.
                return until.value

            until.callbacks.append(StopSimulation.callback)

        try:
            while True:
                # self.step(); the plain method runs without the call. An
                # overridden or patched step() (e.g. for tracing) is honored,
                # also when installed during the run.
                if simpy_plain(self, <PyTypeObject *>Environment, _S_STEP,
                               _STEP):
                    if self._step():
                        _raise_empty()
                else:
                    self.step()
        except StopSimulation as exc:
            return exc.args[0]  # == until.value
        except EmptySchedule:
            if until is not None:
                assert not until.triggered
                raise RuntimeError(
                    f'No scheduled events left but "until" event was not '
                    f'triggered: {until}'
                )
        return None


cdef object _SCHEDULE = Environment.__dict__['schedule']
cdef object _S_SCHEDULE = 'schedule'
cdef object _STEP = Environment.__dict__['step']
cdef object _S_STEP = 'step'
cdef object _NOW = Environment.__dict__['now']
cdef object _S_NOW = 'now'
cdef object _ACTIVE_PROCESS = Environment.__dict__['active_process']
cdef object _S_ACTIVE_PROCESS = 'active_process'


cdef object env_now(object env):
    # env.now
    if simpy_plain_type(env, <PyTypeObject *>Environment, _S_NOW, _NOW):
        return (<Environment>env)._now
    return env.now


cdef object env_active_process(object env):
    # env.active_process
    if simpy_plain_type(env, <PyTypeObject *>Environment, _S_ACTIVE_PROCESS,
                        _ACTIVE_PROCESS):
        return (<Environment>env)._active_proc
    return env.active_process
cdef object _ZERO = 0


cdef int _raise_empty() except -1:
    # What step() raises on an empty queue: EmptySchedule while handling
    # the IndexError of heappop().
    try:
        raise IndexError('index out of range')
    except IndexError:
        raise EmptySchedule()
