"""
This module contains the basic event types used in SimPy.

The base class for all events is :class:`Event`. Though it can be directly
used, there are several specialized subclasses of it.

.. autosummary::

    ~simpy.events.Event
    ~simpy.events.Timeout
    ~simpy.events.Process
    ~simpy.events.AnyOf
    ~simpy.events.AllOf

"""
cimport cython
from cpython.list cimport PyList_GET_ITEM, PyList_GET_SIZE
from cpython.method cimport (
    PyMethod_Check,
    PyMethod_GET_FUNCTION,
    PyMethod_GET_SELF,
)
from cpython.object cimport PyObject, PyTypeObject
from cpython.version cimport PY_VERSION_HEX

import copyreg as _copyreg

from types import FrameType
from types import GeneratorType as _GeneratorType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    Iterator,
    List,
    NewType,
    Optional,
    Tuple,
)

from simpy.exceptions import Interrupt

if TYPE_CHECKING and TYPE_CHECKING != 'SPHINX':  # Avoid circular import
    from simpy.core import Environment, SimTime

cdef extern from "_cfast.h":
    bint simpy_plain(object obj, PyTypeObject *base, object name, object func)
    bint simpy_plain_type(object obj, PyTypeObject *base, object name,
                          object descr)
    object simpy_send(object gen, object arg, object send_name, int *returned)

cdef extern from "Python.h":
    object simpy_method_new "PyMethod_New"(object func, object self)
    bint simpy_type_check "PyObject_TypeCheck"(object o, PyTypeObject *t)

cdef object _ZERO = 0


# Callbacks are called as ``callback(event)``. For a bound method of a
# registered SimPy method (Process._resume, ...) that call ends up in the
# method's C implementation; run_callbacks() calls the implementation
# directly. A registration is (function in the type dict, owner type, impl).
cdef struct _FastCallback:
    PyObject *func
    PyTypeObject *owner
    simpy_cb_impl impl

cdef _FastCallback _fast[8]
cdef int _n_fast = 0
cdef list _fast_keep = []


cdef int register_callback(object func, type owner,
                           simpy_cb_impl impl) except -1:
    global _n_fast
    if _n_fast == 8:
        raise RuntimeError('too many fast callbacks')
    _fast_keep.append(func)
    _fast[_n_fast].func = <PyObject *>func
    _fast[_n_fast].owner = <PyTypeObject *>owner
    _fast[_n_fast].impl = impl
    _n_fast += 1
    return 0


cdef inline int _call_fast(object cb, Event event) except -1:
    # 1 if *cb* is a bound registered method and has been called, else 0.
    cdef PyObject *func = PyMethod_GET_FUNCTION(cb)
    cdef int j
    for j in range(_n_fast):
        if _fast[j].func == func:
            owner = <object>PyMethod_GET_SELF(cb)
            if not simpy_type_check(owner, _fast[j].owner):
                return 0
            _fast[j].impl(owner, event)
            return 1
    return 0


cdef int run_callbacks(Event event, object callbacks) except -1:
    # for callback in callbacks: callback(event)
    cdef Py_ssize_t i = 0
    if type(callbacks) is not list:
        for callback in callbacks:
            callback(event)
        return 0
    while i < PyList_GET_SIZE(callbacks):
        cb = <object>PyList_GET_ITEM(callbacks, i)
        i += 1
        if not (PyMethod_Check(cb) and _call_fast(cb, event)):
            cb(event)
    return 0


cdef inline int _env_schedule(object env, object event, object priority,
                              object delay, int nargs) except -1:
    # ``env.schedule(event[, priority[, delay]])`` with *nargs* arguments.
    if simpy_type_check(env, <PyTypeObject *>Environment):
        return (<Environment>env)._schedule(event, priority, delay, nargs)
    if nargs == 1:
        env.schedule(event)
    elif nargs == 2:
        env.schedule(event, priority)
    else:
        env.schedule(event, priority, delay)
    return 0


cdef bint event_triggered(object event) except -1:
    # event.triggered
    if simpy_plain_type(event, <PyTypeObject *>Event, _S_TRIGGERED,
                        _TRIGGERED):
        return (<Event>event)._value is not PENDING
    return bool(event.triggered)


cdef int raise_no_ok(Event event) except -1:
    # Reading _ok of an event that was never triggered: upstream has no such
    # attribute yet.
    raise _no_attribute(event, '_ok')


cdef int _set_active_proc(object env, object proc) except -1:
    # env._active_proc = proc
    if simpy_type_check(env, <PyTypeObject *>Environment):
        (<Environment>env)._active_proc = proc
    else:
        env._active_proc = proc
    return 0


cdef bint _quiet_env(object env) except -1:
    # True when env.schedule(process) runs no Python code.
    return (simpy_type_check(env, <PyTypeObject *>Environment)
            and (<Environment>env)._quiet())


cdef int _raise_stop_iteration(object value) except -1:
    # What gen.send() raises when the generator returns *value*.
    if value is None:
        raise StopIteration
    raise StopIteration(value)


PENDING: object = object()
"""Unique object to identify pending values of events."""

EventPriority = NewType('EventPriority', int)
EventPriority.__module__ = __name__

URGENT: EventPriority = EventPriority(0)
"""Priority of interrupts and process initialization events."""
NORMAL: EventPriority = EventPriority(1)
"""Default priority used by events."""


@cython.auto_pickle(False)
cdef class Event:
    """An event that may happen at some point in time.

    An event

    - may happen (:attr:`triggered` is ``False``),
    - is going to happen (:attr:`triggered` is ``True``) or
    - has happened (:attr:`processed` is ``True``).

    Every event is bound to an environment *env* and is initially not
    triggered. Events are scheduled for processing by the environment after
    they are triggered by either :meth:`succeed`, :meth:`fail` or
    :meth:`trigger`. These methods also set the *ok* flag and the *value* of
    the event.

    An event has a list of :attr:`callbacks`. A callback can be any callable.
    Once an event gets processed, all callbacks will be invoked with the event
    as the single argument. Callbacks can check if the event was successful by
    examining *ok* and do further processing with the *value* it has produced.

    Failed events are never silently ignored and will raise an exception upon
    being processed. If a callback handles an exception, it must set
    :attr:`defused` to ``True`` to prevent this.

    This class also implements ``__and__()`` (``&``) and ``__or__()`` (``|``).
    If you concatenate two events using one of these operators,
    a :class:`Condition` event is generated that lets you wait for both or one
    of them.

    """

    # Attributes: ``_value`` (PENDING until triggered), ``_ok`` (assigned
    # when triggered) and ``_defused`` (only present once defused).

    def __cinit__(self) -> None:
        # Upstream's class attribute default, also for subclasses that skip
        # __init__.
        self._value = PENDING

    def __init__(self, env: 'Environment'):
        self.env = env
        """The :class:`~simpy.core.Environment` the event lives in."""
        self.callbacks: EventCallbacks = []
        """List of functions that are called when the event is processed."""

    # Upstream creates _ok when the event is triggered; the C field holds
    # None until then, and Python code sees no attribute.

    @property
    def _ok(self):
        ok = self._ok
        if ok is None:
            raise _no_attribute(self, '_ok')
        return ok

    @_ok.setter
    def _ok(self, value):
        self._ok = value

    @_ok.deleter
    def _ok(self):
        if self._ok is None:
            raise _no_attribute(self, '_ok')
        self._ok = None

    # Copying and pickling see what they see of upstream's events: plain
    # Python objects whose instance dict holds the attributes assigned so far
    # (_value and _ok only once set).

    def __getstate__(self):
        state = {}
        for name in _state_fields_of(type(self)):
            try:
                state[name] = object.__getattribute__(self, name)
            except AttributeError:
                pass  # Not assigned yet (_ok).
        if self._value is PENDING:
            del state['_value']
        state.update(vars(self))
        return state

    def __setstate__(self, state):
        fields = _state_fields_of(type(self))
        d = vars(self)
        for key, value in state.items():
            if key in fields:
                object.__setattr__(self, key, value)
            else:
                d[key] = value

    def __reduce_ex__(self, protocol):
        if protocol >= 2 or type(self).__reduce__ is not object.__reduce__:
            return object.__reduce_ex__(self, protocol)
        return _copyreg.__newobj__, (type(self),), self.__getstate__()

    def __repr__(self) -> str:
        """Return the description of the event (see :meth:`_desc`) with the id
        of the event."""
        return f'<{self._desc()} object at {id(self):#x}>'

    def _desc(self) -> str:
        """Return a string *Event()*."""
        return f'{self.__class__.__name__}()'

    @property
    def triggered(self) -> bool:
        """Becomes ``True`` if the event has been triggered and its callbacks
        are about to be invoked."""
        return self._value is not PENDING

    @property
    def processed(self) -> bool:
        """Becomes ``True`` if the event has been processed (e.g., its
        callbacks have been invoked)."""
        return self.callbacks is None

    @property
    def ok(self) -> bool:
        """Becomes ``True`` when the event has been triggered successfully.

        A "successful" event is one triggered with :meth:`succeed()`.

        :raises AttributeError: if accessed before the event is triggered.

        """
        ok = self._ok
        if ok is None:
            # An unset _ok is stored as None; this is the error of upstream's
            # failing attribute lookup.
            raise _no_attribute(self, '_ok')
        return ok

    @property
    def defused(self) -> bool:
        """Becomes ``True`` when the failed event's exception is "defused".

        When an event fails (i.e. with :meth:`fail()`), the failed event's
        `value` is an exception that will be re-raised when the
        :class:`~simpy.core.Environment` processes the event (i.e. in
        :meth:`~simpy.core.Environment.step()`).

        It is also possible for the failed event's exception to be defused by
        setting :attr:`defused` to ``True`` from an event callback. Doing so
        prevents the event's exception from being re-raised when the event is
        processed by the :class:`~simpy.core.Environment`.

        """
        return hasattr(self, '_defused')

    @defused.setter
    def defused(self, value: bool) -> None:
        self._defused = True

    @property
    def value(self) -> Optional[Any]:
        """The value of the event if it is available.

        The value is available when the event has been triggered.

        Raises :exc:`AttributeError` if the value is not yet available.

        """
        if self._value is PENDING:
            raise AttributeError(f'Value of {self} is not yet available')
        return self._value

    def trigger(self, event: 'Event') -> None:
        """Trigger the event with the state and value of the provided *event*.
        Return *self* (this event instance).

        This method can be used directly as a callback function to trigger
        chain reactions.

        """
        self._ok = event._ok
        self._value = event._value
        _env_schedule(self.env, self, NORMAL, _ZERO, 1)

    cpdef succeed(self, value=None):
        """Set the event's value, mark it as successful and schedule it for
        processing by the environment. Returns the event instance.

        Raises :exc:`RuntimeError` if this event has already been triggerd.

        """
        if self._value is not PENDING:
            raise RuntimeError(f'{self} has already been triggered')

        self._ok = True
        self._value = value
        _env_schedule(self.env, self, NORMAL, _ZERO, 1)
        return self

    def fail(self, exception: Exception) -> 'Event':
        """Set *exception* as the events value, mark it as failed and schedule
        it for processing by the environment. Returns the event instance.

        Raises :exc:`ValueError` if *exception* is not an :exc:`Exception`.

        Raises :exc:`RuntimeError` if this event has already been triggered.

        """
        if self._value is not PENDING:
            raise RuntimeError(f'{self} has already been triggered')
        if not isinstance(exception, BaseException):
            raise ValueError(f'{exception} is not an exception.')
        self._ok = False
        self._value = exception
        _env_schedule(self.env, self, NORMAL, _ZERO, 1)
        return self

    def __and__(self, other: 'Event') -> 'Condition':
        """Return a :class:`~simpy.events.Condition` that will be triggered if
        both, this event and *other*, have been processed."""
        return Condition(self.env, Condition.all_events, [self, other])

    def __or__(self, other: 'Event') -> 'Condition':
        """Return a :class:`~simpy.events.Condition` that will be triggered if
        either this event or *other* have been processed (or even both, if they
        happened concurrently)."""
        return Condition(self.env, Condition.any_events, [self, other])


# The C fields that hold each event type's state, for __getstate__.
cdef dict _state_fields = {}


cdef int register_state_fields(type cls, tuple names) except -1:
    _state_fields[cls] = names
    return 0


cdef list _state_fields_of(type cls):
    fields = []
    for base in reversed(cls.__mro__):
        names = _state_fields.get(base)
        if names is not None:
            fields.extend(<tuple>names)
    return fields


cdef object _no_attribute(object obj, str name):
    # The AttributeError that looking up a missing attribute raises.
    msg = f"'{type(obj).__name__}' object has no attribute '{name}'"
    if PY_VERSION_HEX >= 0x030A0000:
        return AttributeError(msg, name=name, obj=obj)
    return AttributeError(msg)

EventCallback = Callable[[Event], None]
EventCallbacks = List[EventCallback]


@cython.auto_pickle(False)
cdef class Timeout(Event):
    """A :class:`~simpy.events.Event` that gets processed after a *delay* has
    passed.

    This event is automatically triggered when it is created.


    """

    def __init__(
        self,
        env: 'Environment',
        delay: 'SimTime',
        value: Optional[Any] = None,
    ):
        if delay < 0:
            raise ValueError(f'Negative delay {delay}')
        # NOTE: The following initialization code is inlined from
        # Event.__init__() for performance reasons.
        self.env = env
        self.callbacks: EventCallbacks = []
        self._value = value
        self._delay = delay
        self._ok = True
        _env_schedule(self.env, self, NORMAL, delay, 3)

    def _desc(self) -> str:
        """Return a string *Timeout(delay[, value=value])*."""
        value_str = '' if self._value is None else f', value={self.value}'
        return f'{self.__class__.__name__}({self._delay}{value_str})'


@cython.auto_pickle(False)
cdef class Initialize(Event):
    """Initializes a process. Only used internally by :class:`Process`.

    This event is automatically triggered when it is created.

    """

    def __init__(self, env: 'Environment', process: 'Process'):
        # NOTE: The following initialization code is inlined from
        # Event.__init__() for performance reasons.
        self.env = env
        if isinstance(process, Process):
            self.callbacks = [(<Process>process)._resume_bound()]
        else:
            self.callbacks = [process._resume]
        self._value: Any = None

        # The initialization events needs to be scheduled as urgent so that it
        # will be handled before interrupts. Otherwise, a process whose
        # generator has not yet been started could be interrupted.
        self._ok = True
        _env_schedule(self.env, self, URGENT, _ZERO, 2)


class Interruption(Event):
    """Immediately schedules an :class:`~simpy.exceptions.Interrupt` exception
    with the given *cause* to be thrown into *process*.

    This event is automatically triggered when it is created.

    """

    def __init__(self, process: 'Process', cause: Optional[Any]):
        # NOTE: The following initialization code is inlined from
        # Event.__init__() for performance reasons.
        self.env = process.env
        self.callbacks: EventCallbacks = [self._interrupt]
        self._value = Interrupt(cause)
        self._ok = False
        self._defused = True

        if process._value is not PENDING:
            raise RuntimeError(
                f'{process} has terminated and cannot be interrupted.'
            )

        if process is self.env.active_process:
            raise RuntimeError('A process is not allowed to interrupt itself.')

        self.process = process
        _env_schedule(self.env, self, URGENT, _ZERO, 2)

    def _interrupt(self, event: Event) -> None:
        # Ignore dead processes. Multiple concurrently scheduled interrupts
        # cause this situation. If the process dies while handling the first
        # one, the remaining interrupts must be ignored.
        if self.process._value is not PENDING:
            return

        # A process never expects an interrupt and is always waiting for a
        # target event. Remove the process from the callbacks of the target.
        self.process._target.callbacks.remove(self.process._resume)

        self.process._resume(self)


ProcessGenerator = Generator[Event, Any, Any]


@cython.auto_pickle(False)
cdef class Process(Event):
    """Process an event yielding generator.

    A generator (also known as a coroutine) can suspend its execution by
    yielding an event. ``Process`` will take care of resuming the generator
    with the value of that event once it has happened. The exception of failed
    events is thrown into the generator.

    ``Process`` itself is an event, too. It is triggered, once the generator
    returns or raises an exception. The value of the process is the return
    value of the generator or the exception, respectively.

    Processes can be interrupted during their execution by :meth:`interrupt`.

    """

    def __init__(self, env: 'Environment', generator: ProcessGenerator):
        if type(generator) is not _GeneratorType and not hasattr(
                generator, 'throw'):
            # Implementation note: Python implementations differ in the
            # generator types they provide. Cython adds its own generator type
            # in addition to the CPython type, which renders a type check
            # impractical. To work around this issue, we check for attribute
            # name instead of type and optimistically assume that all objects
            # with a ``throw`` attribute are generators.
            # Remove this workaround if it causes issues in production!
            raise ValueError(f'{generator} is not a generator.')

        # NOTE: The following initialization code is inlined from
        # Event.__init__() for performance reasons.
        self.env = env
        self.callbacks: EventCallbacks = []

        self._generator = generator

        # Schedule the start of the execution of the process. Same as
        # Initialize(env, self).
        cdef Initialize init = Initialize.__new__(Initialize)
        init.env = env
        init.callbacks = [self._resume_bound()]
        init._value = None
        init._ok = True
        _env_schedule(init.env, init, URGENT, _ZERO, 2)
        self._target = init

    def _desc(self) -> str:
        """Return a string *Process(process_func_name)*."""
        gen_name: str = self._generator.__name__  # type: ignore
        return f'{self.__class__.__name__}({gen_name})'

    @property
    def target(self) -> Event:
        """The event that the process is currently waiting for.

        Returns ``None`` if the process is dead, or it is currently being
        interrupted.

        """
        return self._target

    @property
    def is_alive(self) -> bool:
        """``True`` until the process generator exits."""
        return self._value is PENDING

    def interrupt(self, cause: Optional[Any] = None) -> None:
        """Interrupt this process optionally providing a *cause*.

        A process cannot be interrupted if it already terminated. A process can
        also not interrupt itself. Raise a :exc:`RuntimeError` in these
        cases.

        """
        Interruption(self, cause)

    def _resume(self, event: Event) -> None:
        """Resumes the execution of the process with the value of *event*. If
        the process generator exits, the process itself will get triggered with
        the return value or the exception of the generator."""
        self._do_resume(event)

    cdef object _resume_bound(self):
        # ``self._resume``; the bound method is cached while the attribute is
        # the plain method. Dropped when the generator exits.
        if simpy_plain(self, <PyTypeObject *>Process, _S_RESUME, _RESUME):
            cb = self._resume_cb
            if cb is None:
                cb = self._resume_cb = simpy_method_new(_RESUME, self)
            return cb
        return getattr(self, '_resume')

    cdef int _do_resume(self, object event) except -1:  # noqa: C901
        cdef int returned = 0
        # Mark the current process as active.
        _set_active_proc(self.env, self)

        while True:
            # Get next event from process
            try:
                if isinstance(event, Event):
                    ok = (<Event>event)._ok
                    if ok is None:
                        raise_no_ok(<Event>event)
                else:
                    ok = event._ok
                if ok:
                    if isinstance(event, Event):
                        value = (<Event>event)._value
                    else:
                        value = event._value
                    next_event = simpy_send(self._generator, value, _S_SEND,
                                            &returned)
                    if returned and not _quiet_env(self.env):
                        # Upstream schedules the terminated process while
                        # handling the StopIteration, where Python code that
                        # scheduling runs can see it.
                        returned = 0
                        _raise_stop_iteration(next_event)
                else:
                    # The process has no choice but to handle the failed event
                    # (or fail itself).
                    event._defused = True

                    # Create an exclusive copy of the exception for this
                    # process to prevent traceback modifications by other
                    # processes.
                    exc = type(event._value)(*event._value.args)
                    exc.__cause__ = event._value
                    next_event = self._generator.throw(exc)
            except StopIteration as e:
                # Process has terminated.
                self._ok = True
                self._value = e.args[0] if len(e.args) else None
                self._resume_cb = None
                _env_schedule(self.env, self, NORMAL, _ZERO, 1)
                next_event = None
                break
            except BaseException as e:
                # Process has failed.
                self._ok = False
                tb = e.__traceback__
                # Strip the frame of this function from the traceback as it
                # does not add any useful information.
                e.__traceback__ = tb.tb_next if tb else None
                self._value = e
                self._resume_cb = None
                _env_schedule(self.env, self, NORMAL, _ZERO, 1)
                next_event = None
                break

            if returned:
                # Process has terminated (the generator returned).
                self._ok = True
                self._value = next_event
                self._resume_cb = None
                _env_schedule(self.env, self, NORMAL, _ZERO, 1)
                next_event = None
                break

            # Process returned another event to wait upon.
            if isinstance(next_event, Event):
                callbacks = (<Event>next_event).callbacks
                if callbacks is not None:
                    # The event has not yet been triggered. Register callback
                    # to resume the process if that happens.
                    callbacks.append(self._resume_bound())
                    break
            else:
                try:
                    # Be optimistic and blindly access the callbacks attribute.
                    if next_event.callbacks is not None:
                        # The event has not yet been triggered. Register
                        # callback to resume the process if that happens.
                        next_event.callbacks.append(self._resume_bound())
                        break
                except AttributeError:
                    # Our optimism didn't work out, figure out what went wrong
                    # and inform the user.
                    if hasattr(next_event, 'callbacks'):
                        raise

                    msg = f'Invalid yield value "{next_event}"'
                    descr = _describe_frame(self._generator.gi_frame)
                    raise RuntimeError(f'\n{descr}{msg}') from None
            # The event has already been processed: resume with its value.
            event = next_event

        self._target = next_event
        _set_active_proc(self.env, None)
        return 0


cdef object _RESUME = Process.__dict__['_resume']
cdef object _TRIGGERED = Event.__dict__['triggered']
cdef object _S_TRIGGERED = 'triggered'
cdef object _S_RESUME = '_resume'
cdef object _S_SEND = 'send'


cdef int _resume_impl(object owner, Event event) except -1:
    return (<Process>owner)._do_resume(event)


register_callback(_RESUME, Process, _resume_impl)
register_state_fields(Event, ('env', 'callbacks', '_value', '_ok'))
register_state_fields(Timeout, ('_delay',))
register_state_fields(Process, ('_generator', '_target'))


class ConditionValue:
    """Result of a :class:`~simpy.events.Condition`. It supports convenient
    dict-like access to the triggered events and their values. The events are
    ordered by their occurrences in the condition."""

    def __init__(self) -> None:
        self.events: List[Event] = []

    def __getitem__(self, key: Event) -> Any:
        if key not in self.events:
            raise KeyError(str(key))

        return key._value

    def __contains__(self, key: Event) -> bool:
        return key in self.events

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ConditionValue):
            return self.events == other.events
        elif isinstance(other, dict):
            return self.todict() == other
        else:
            return NotImplemented

    def __repr__(self) -> str:
        return f'<ConditionValue {self.todict()}>'

    def __iter__(self) -> Iterator[Event]:
        return self.keys()

    def keys(self) -> Iterator[Event]:
        return (event for event in self.events)

    def values(self) -> Iterator[Any]:
        return (event._value for event in self.events)

    def items(self) -> Iterator[Tuple[Event, Any]]:
        return ((event, event._value) for event in self.events)

    def todict(self) -> Dict[Event, Any]:
        return dict((event, event._value) for event in self.events)


@cython.auto_pickle(False)
cdef class Condition(Event):
    """An event that gets triggered once the condition function *evaluate*
    returns ``True`` on the given list of *events*.

    The value of the condition event is an instance of :class:`ConditionValue`
    which allows convenient access to the input events and their values. The
    :class:`ConditionValue` will only contain entries for those events that
    occurred before the condition is processed.

    If one of the events fails, the condition also fails and forwards the
    exception of the failing event.

    The *evaluate* function receives the list of target events and the number
    of processed events in this list: ``evaluate(events, processed_count)``. If
    it returns ``True``, the condition is triggered. The
    :func:`Condition.all_events()` and :func:`Condition.any_events()` functions
    are used to implement *and* (``&``) and *or* (``|``) for events.

    Condition events can be nested.

    """

    def __init__(
        self,
        env: 'Environment',
        evaluate: Callable[[Tuple[Event, ...], int], bool],
        events: Iterable[Event],
    ):
        super().__init__(env)
        self._evaluate = evaluate
        self._events = tuple(events)
        self._count = 0

        if not self._events:
            # Immediately succeed if no events are provided.
            self.succeed(ConditionValue())
            return

        # Check if events belong to the same environment.
        for event in self._events:
            if self.env != event.env:
                raise ValueError(
                    'It is not allowed to mix events from different '
                    'environments'
                )

        # Check if the condition is met for each processed event. Attach
        # _check() as a callback otherwise.
        for event in self._events:
            if event.callbacks is None:
                self._check(event)
            else:
                event.callbacks.append(self._check)

        # Register a callback which will build the value of this condition
        # after it has been triggered.
        assert isinstance(self.callbacks, list)
        self.callbacks.append(self._build_value)

    def _desc(self) -> str:
        """Return a string *Condition(evaluate, [events])*."""
        return (
            f'{self.__class__.__name__}('
            f'{self._evaluate.__name__}, {self._events})'
        )

    def _populate_value(self, value: ConditionValue) -> None:
        """Populate the *value* by recursively visiting all nested
        conditions."""

        for event in self._events:
            if isinstance(event, Condition):
                event._populate_value(value)
            elif event.callbacks is None:
                value.events.append(event)

    def _build_value(self, event: Event) -> None:
        """Build the value of this condition."""
        self._remove_check_callbacks()
        if event._ok:
            self._value = ConditionValue()
            self._populate_value(self._value)

    def _remove_check_callbacks(self) -> None:
        """Remove _check() callbacks from events recursively.

        Once the condition has triggered, the condition's events no longer need
        to have _check() callbacks. Removing the _check() callbacks is
        important to break circular references between the condition and
        untriggered events.

        """
        for event in self._events:
            if event.callbacks and self._check in event.callbacks:
                event.callbacks.remove(self._check)
            if isinstance(event, Condition):
                event._remove_check_callbacks()

    def _check(self, event: Event) -> None:
        """Check if the condition was already met and schedule the *event* if
        so."""
        if self._value is not PENDING:
            return

        self._count += 1

        if not event._ok:
            # Abort if the event has failed.
            event._defused = True
            self.fail(event._value)
        elif self._evaluate(self._events, self._count):
            # The condition has been met. The _build_value() callback will
            # populate the ConditionValue once this condition is processed.
            self.succeed()

    @staticmethod
    def all_events(events: Tuple[Event, ...], count: int) -> bool:
        """An evaluation function that returns ``True`` if all *events* have
        been triggered."""
        return len(events) == count

    @staticmethod
    def any_events(events: Tuple[Event, ...], count: int) -> bool:
        """An evaluation function that returns ``True`` if at least one of
        *events* has been triggered."""
        return count > 0 or len(events) == 0


class AllOf(Condition):
    """A :class:`~simpy.events.Condition` event that is triggered if all of
    a list of *events* have been successfully triggered. Fails immediately if
    any of *events* failed.

    """

    def __init__(self, env: 'Environment', events: Iterable[Event]):
        super().__init__(env, Condition.all_events, events)


class AnyOf(Condition):
    """A :class:`~simpy.events.Condition` event that is triggered if any of
    a list of *events* has been successfully triggered. Fails immediately if
    any of *events* failed.

    """

    def __init__(self, env: 'Environment', events: Iterable[Event]):
        super().__init__(env, Condition.any_events, events)


def _describe_frame(frame: FrameType) -> str:
    """Print filename, line number and function name of a stack frame."""
    filename, name = frame.f_code.co_filename, frame.f_code.co_name
    lineno = frame.f_lineno

    with open(filename) as f:
        for no, line in enumerate(f):
            if no + 1 == lineno:
                break

    return (
        f'  File "{filename}", line {lineno}, in {name}\n'
        f'    {line.strip()}\n'
    )


register_state_fields(Condition, ('_evaluate', '_events', '_count'))
