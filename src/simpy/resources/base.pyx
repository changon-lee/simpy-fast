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
    Generic,
    MutableSequence,
    Optional,
    Type,
    TypeVar,
    Union,
)

from simpy.core import BoundClass, Environment
from simpy.events import PENDING, Event, Process

ResourceType = TypeVar('ResourceType', bound='BaseResource')


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
        # NOTE: Event.__init__() is inlined for performance reasons.
        self.resource = resource
        res = self.resource  # Same object; a typed local when compiled.
        env = res._env
        self.env = env
        self.callbacks = [res._trigger_get]
        self.proc = env._active_proc

        res.put_queue.append(self)
        res._trigger_put(None)

    @classmethod
    def __class_getitem__(cls, item: object) -> type:
        # Keeps annotations such as ``Put[Resource]`` working. The class is
        # not a typing.Generic so that Cython can compile it to an extension
        # type; it is still a context manager through __enter__/__exit__.
        return cls

    def __enter__(self) -> 'Put':
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self.cancel()
        return None

    def cancel(self) -> None:
        """Cancel this put request.

        This method has to be called if the put request must be aborted, for
        example if a process needs to handle an exception like an
        :class:`~simpy.exceptions.Interrupt`.

        If the put request was created in a :keyword:`with` statement, this
        method is called automatically.

        """
        if self._value is PENDING:
            self.resource.put_queue.remove(self)


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
        # NOTE: Event.__init__() is inlined for performance reasons.
        self.resource = resource
        res = self.resource  # Same object; a typed local when compiled.
        env = res._env
        self.env = env
        self.callbacks = [res._trigger_put]
        self.proc = env._active_proc

        res.get_queue.append(self)
        res._trigger_get(None)

    @classmethod
    def __class_getitem__(cls, item: object) -> type:
        # Keeps annotations such as ``Get[Resource]`` working. The class is
        # not a typing.Generic so that Cython can compile it to an extension
        # type; it is still a context manager through __enter__/__exit__.
        return cls

    def __enter__(self) -> 'Get':
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self.cancel()
        return None

    def cancel(self) -> None:
        """Cancel this get request.

        This method has to be called if the get request must be aborted, for
        example if a process needs to handle an exception like an
        :class:`~simpy.exceptions.Interrupt`.

        If the get request was created in a :keyword:`with` statement, this
        method is called automatically.

        """
        if self._value is PENDING:
            self.resource.get_queue.remove(self)


PutType = TypeVar('PutType', bound=Put)
GetType = TypeVar('GetType', bound=Get)


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
    def __class_getitem__(cls, item: object) -> type:
        # Keeps annotations such as ``BaseResource[Put, Get]`` working. Not a
        # typing.Generic so that Cython can compile it to an extension type.
        return cls

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
        cdef Event put_event
        """This method is called once a new put event has been created or a get
        event has been processed.

        The method iterates over all put events in the :attr:`put_queue` and
        calls :meth:`_do_put` to check if the conditions for the event are met.
        If :meth:`_do_put` returns ``False``, the iteration is stopped early.
        """

        # Maintain queue invariant: All put requests must be untriggered.
        # This code is not very pythonic because the queue interface should be
        # simple (only append(), pop(), __getitem__() and __len__() are
        # required).
        put_queue = self.put_queue
        do_put = self._do_put
        idx = 0
        while idx < len(put_queue):
            put_event = put_queue[idx]
            proceed = do_put(put_event)
            if put_event._value is PENDING:
                idx += 1
            elif put_queue.pop(idx) is not put_event:
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
        cdef Event get_event
        """Trigger get events.

        This method is called once a new get event has been created or a put
        event has been processed.

        The method iterates over all get events in the :attr:`get_queue` and
        calls :meth:`_do_get` to check if the conditions for the event are met.
        If :meth:`_do_get` returns ``False``, the iteration is stopped early.
        """

        # Maintain queue invariant: All get requests must be untriggered.
        # This code is not very pythonic because the queue interface should be
        # simple (only append(), pop(), __getitem__() and __len__() are
        # required).
        get_queue = self.get_queue
        do_get = self._do_get
        idx = 0
        while idx < len(get_queue):
            get_event = get_queue[idx]
            proceed = do_get(get_event)
            if get_event._value is PENDING:
                idx += 1
            elif get_queue.pop(idx) is not get_event:
                raise RuntimeError('Get queue invariant violated')

            if not proceed:
                break
