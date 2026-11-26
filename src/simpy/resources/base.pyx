"""
Base classes of for SimPy's shared resource types.

:class:`BaseResource` defines the abstract base resource. It supports *get* and
*put* requests, which return :class:`Put` and :class:`Get` events respectively.
These events are triggered once the request has been completed.

"""
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    ClassVar,
    ContextManager,
    Generic,
    MutableSequence,
    Optional,
    Type,
    TypeVar,
    Union,
)

cimport cython
from cpython.object cimport PyObject, PyTypeObject

from simpy.core cimport env_active_process
from simpy.events cimport event_triggered, register_state_fields
from simpy.core import BoundClass, Environment
from simpy.events import Event, Process

cdef extern from "_cfast.h":
    PyObject *simpy_mro_next(object obj, PyTypeObject *cls)
    bint simpy_plain(object obj, PyTypeObject *base, object name, object func)
    object simpy_list_pop(object list, Py_ssize_t i)
    int simpy_ne(object a, object b) except -1

try:
    from types import GenericAlias as _GenericAlias
except ImportError:  # Python < 3.9
    def _GenericAlias(cls, item):
        return cls

# Cython binds cimported types at C level only; upstream also has them as
# module attributes.
globals().update(Environment=Environment, Event=Event)

ResourceType = TypeVar('ResourceType', bound='BaseResource')
ResourceType.__module__ = __name__


@cython.auto_pickle(False)
cdef class Put(Event):
    """Generic event for requesting to put something into the *resource*.

    This event (and all of its subclasses) can act as context manager and can
    be used with the :keyword:`with` statement to automatically cancel the
    request if an exception (like an :class:`simpy.exceptions.Interrupt` for
    example) occurs:

    .. code-block:: python

        with res.put(item) as request:
            yield request

    """

    def __init__(self, resource: ResourceType):
        put_init(self, resource)

    @classmethod
    def __class_getitem__(cls, item: object) -> object:
        # ``Put[Resource]``: the class is not a typing.Generic, so that
        # Cython can compile it to an extension type.
        return _GenericAlias(cls, item)

    def __enter__(self) -> 'Put':
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        _put_cancel(self)  # self.cancel()
        return None

    def cancel(self) -> None:
        """Cancel this put request.

        This method has to be called if the put request must be aborted, for
        example if a process needs to handle an exception like an
        :class:`~simpy.exceptions.Interrupt`.

        If the put request was created in a :keyword:`with` statement, this
        method is called automatically.

        """
        if not event_triggered(self):
            self.resource.put_queue.remove(self)


@cython.auto_pickle(False)
cdef class Get(Event):
    """Generic event for requesting to get something from the *resource*.

    This event (and all of its subclasses) can act as context manager and can
    be used with the :keyword:`with` statement to automatically cancel the
    request if an exception (like an :class:`simpy.exceptions.Interrupt` for
    example) occurs:

    .. code-block:: python

        with res.get() as request:
            item = yield request

    """

    def __init__(self, resource: ResourceType):
        get_init(self, resource)

    @classmethod
    def __class_getitem__(cls, item: object) -> object:
        # ``Get[Resource]``: the class is not a typing.Generic, so that
        # Cython can compile it to an extension type.
        return _GenericAlias(cls, item)

    def __enter__(self) -> 'Get':
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        _get_cancel(self)  # self.cancel()
        return None

    def cancel(self) -> None:
        """Cancel this get request.

        This method has to be called if the get request must be aborted, for
        example if a process needs to handle an exception like an
        :class:`~simpy.exceptions.Interrupt`.

        If the get request was created in a :keyword:`with` statement, this
        method is called automatically.

        """
        if not event_triggered(self):
            self.resource.get_queue.remove(self)


PutType = TypeVar('PutType', bound=Put)
PutType.__module__ = __name__
GetType = TypeVar('GetType', bound=Get)
GetType.__module__ = __name__


cdef class BaseResource:
    """Abstract base class for a shared resource.

    You can :meth:`put()` something into the resources or :meth:`get()`
    something out of it. Both methods return an event that is triggered once
    the operation is completed. If a :meth:`put()` request cannot complete
    immediately (for example if the resource has reached a capacity limit) it
    is enqueued in the :attr:`put_queue` for later processing. Likewise for
    :meth:`get()` requests.

    Subclasses can customize the resource by:

    - providing custom :attr:`PutQueue` and :attr:`GetQueue` types,
    - providing custom :class:`Put` respectively :class:`Get` events,
    - and implementing the request processing behaviour through the methods
      ``_do_get()`` and ``_do_put()``.

    """

    PutQueue: ClassVar[Type[MutableSequence]] = list
    """The type to be used for the :attr:`put_queue`. It is a plain
    :class:`list` by default. The type must support index access (e.g.
    ``__getitem__()`` and ``__len__()``) as well as provide ``append()`` and
    ``pop()`` operations."""

    GetQueue: ClassVar[Type[MutableSequence]] = list
    """The type to be used for the :attr:`get_queue`. It is a plain
    :class:`list` by default. The type must support index access (e.g.
    ``__getitem__()`` and ``__len__()``) as well as provide ``append()`` and
    ``pop()`` operations."""

    @classmethod
    def __class_getitem__(cls, item: object) -> object:
        # ``BaseResource[Put, Get]``: the class is not a typing.Generic, so
        # that Cython can compile it to an extension type.
        return _GenericAlias(cls, item)

    def __init__(self, env: Environment, capacity: Union[float, int]):
        self._env = env
        self._capacity = capacity
        self.put_queue: MutableSequence[PutType] = self.PutQueue()
        """Queue of pending *put* requests."""
        self.get_queue: MutableSequence[GetType] = self.GetQueue()
        """Queue of pending *get* requests."""

        # Bind event constructors as methods
        BoundClass.bind_early(self)

    @property
    def capacity(self) -> Union[float, int]:
        """Maximum capacity of the resource."""
        return self._capacity

    if TYPE_CHECKING:

        def put(self) -> Put:
            """Request to put something into the resource and return a
            :class:`Put` event, which gets triggered once the request
            succeeds."""
            return Put(self)

        def get(self) -> Get:
            """Request to get something from the resource and return a
            :class:`Get` event, which gets triggered once the request
            succeeds."""
            return Get(self)

    else:
        put = BoundClass(Put)
        get = BoundClass(Get)

    cpdef _do_put(self, event):
        """Perform the *put* operation.

        This method needs to be implemented by subclasses. If the conditions
        for the put *event* are met, the method must trigger the event (e.g.
        call :meth:`Event.succeed()` with an appropriate value).

        This method is called by :meth:`_trigger_put` for every event in the
        :attr:`put_queue`, as long as the return value does not evaluate
        ``False``.
        """
        raise NotImplementedError(self)

    cpdef _trigger_put(self, get_event):
        """This method is called once a new put event has been created or a get
        event has been processed.

        The method iterates over all put events in the :attr:`put_queue` and
        calls :meth:`_do_put` to check if the conditions for the event are met.
        If :meth:`_do_put` returns ``False``, the iteration is stopped early.
        """
        cdef Py_ssize_t idx

        # Maintain queue invariant: All put requests must be untriggered.
        # This code is not very pythonic because the queue interface should be
        # simple (only append(), pop(), __getitem__() and __len__() are
        # required).
        idx = 0
        while idx < len(self.put_queue):
            put_event = self.put_queue[idx]
            proceed = self._do_put(put_event)
            if not event_triggered(put_event):
                idx += 1
            elif simpy_ne(_pop(self.put_queue, idx), put_event):
                raise RuntimeError('Put queue invariant violated')

            if not proceed:
                break

    cpdef _do_get(self, event):
        """Perform the *get* operation.

        This method needs to be implemented by subclasses. If the conditions
        for the get *event* are met, the method must trigger the event (e.g.
        call :meth:`Event.succeed()` with an appropriate value).

        This method is called by :meth:`_trigger_get` for every event in the
        :attr:`get_queue`, as long as the return value does not evaluate
        ``False``.
        """
        raise NotImplementedError(self)

    cpdef _trigger_get(self, put_event):
        """Trigger get events.

        This method is called once a new get event has been created or a put
        event has been processed.

        The method iterates over all get events in the :attr:`get_queue` and
        calls :meth:`_do_get` to check if the conditions for the event are met.
        If :meth:`_do_get` returns ``False``, the iteration is stopped early.
        """
        cdef Py_ssize_t idx

        # Maintain queue invariant: All get requests must be untriggered.
        # This code is not very pythonic because the queue interface should be
        # simple (only append(), pop(), __getitem__() and __len__() are
        # required).
        idx = 0
        while idx < len(self.get_queue):
            get_event = self.get_queue[idx]
            proceed = self._do_get(get_event)
            if not event_triggered(get_event):
                idx += 1
            elif simpy_ne(_pop(self.get_queue, idx), get_event):
                raise RuntimeError('Get queue invariant violated')

            if not proceed:
                break


cdef int put_init(Put self, object resource) except -1:
    # Put.__init__(self, resource); subclasses call it for super().__init__().
    cdef BaseResource res
    cdef bint typed = isinstance(resource, BaseResource)
    if simpy_mro_next(self, <PyTypeObject *>Put) is <PyObject *>Event:
        # super().__init__(resource._env) runs Event.__init__(): inlined.
        if typed:
            self.env = (<BaseResource>resource)._env
        else:
            self.env = resource._env
        self.callbacks = []
    else:
        super(Put, self).__init__(resource._env)
    self.resource = resource
    self.proc = env_active_process(self.env)
    if typed:
        res = <BaseResource>resource
        res.put_queue.append(self)
        self.callbacks.append(res._trigger_get)
        res._trigger_put(None)
    else:
        resource.put_queue.append(self)
        self.callbacks.append(resource._trigger_get)
        resource._trigger_put(None)
    return 0


cdef int get_init(Get self, object resource) except -1:
    # Get.__init__(self, resource); subclasses call it for super().__init__().
    cdef BaseResource res
    cdef bint typed = isinstance(resource, BaseResource)
    if simpy_mro_next(self, <PyTypeObject *>Get) is <PyObject *>Event:
        # super().__init__(resource._env) runs Event.__init__(): inlined.
        if typed:
            self.env = (<BaseResource>resource)._env
        else:
            self.env = resource._env
        self.callbacks = []
    else:
        super(Get, self).__init__(resource._env)
    self.resource = resource
    self.proc = env_active_process(self.env)
    if typed:
        res = <BaseResource>resource
        res.get_queue.append(self)
        self.callbacks.append(res._trigger_put)
        res._trigger_get(None)
    else:
        resource.get_queue.append(self)
        self.callbacks.append(resource._trigger_put)
        resource._trigger_get(None)
    return 0


cdef int _put_cancel(Put self) except -1:
    # self.cancel()
    if simpy_plain(self, <PyTypeObject *>Put, _S_CANCEL, _PUT_CANCEL):
        if not event_triggered(self):
            self.resource.put_queue.remove(self)
    else:
        self.cancel()
    return 0


cdef int _get_cancel(Get self) except -1:
    # self.cancel()
    if simpy_plain(self, <PyTypeObject *>Get, _S_CANCEL, _GET_CANCEL):
        if not event_triggered(self):
            self.resource.get_queue.remove(self)
    else:
        self.cancel()
    return 0


cdef int put_exit(Put self) except -1:
    # Put.__exit__(self, ...)
    return _put_cancel(self)


cdef object _S_CANCEL = 'cancel'
cdef object _PUT_CANCEL = Put.__dict__['cancel']
cdef object _GET_CANCEL = Get.__dict__['cancel']


cdef inline object _pop(object queue, Py_ssize_t idx):
    # queue.pop(idx)
    if type(queue) is list:
        return simpy_list_pop(queue, idx)
    return queue.pop(idx)


register_state_fields(Put, ('resource', 'proc'))
register_state_fields(Get, ('resource', 'proc'))
