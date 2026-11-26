"""
Shared resources supporting priorities and preemption.

These resources can be used to limit the number of processes using them
concurrently. A process needs to *request* the usage right to a resource. Once
the usage right is not needed any more it has to be *released*. A gas station
can be modelled as a resource with a limited amount of fuel-pumps. Vehicles
arrive at the gas station and request to use a fuel-pump. If all fuel-pumps are
in use, the vehicle needs to wait until one of the users has finished refueling
and releases its fuel-pump.

These resources can be used by a limited number of processes at a time.
Processes *request* these resources to become a user and have to *release* them
once they are done. For example, a gas station with a limited number of fuel
pumps can be modeled with a `Resource`. Arriving vehicles request a fuel-pump.
Once one is available they refuel. When they are done, the release the
fuel-pump and leave the gas station.

Requesting a resource is modelled as "putting a process' token into the
resources" and releasing a resources correspondingly as "getting a process'
token out of the resource". Thus, calling ``request()``/``release()`` is
equivalent to calling ``put()``/``get()``. Note, that releasing a resource will
always succeed immediately, no matter if a process is actually using a resource
or not.

Besides :class:`Resource`, there is a :class:`PriorityResource`, where
processes can define a request priority, and a :class:`PreemptiveResource`
whose resource users can be preempted by requests with a higher priority.

"""
from operator import attrgetter as _attrgetter
from types import TracebackType
from typing import TYPE_CHECKING, Any, List, Optional, Type

cimport cython
from cpython.list cimport PyList_GET_ITEM, PyList_GET_SIZE, PyList_Insert
from cpython.object cimport PyObject, PyTypeObject

from simpy.core cimport env_now
from simpy.events cimport register_state_fields
from simpy.resources.base cimport BaseResource

cdef extern from "_cfast.h":
    bint simpy_plain_type(object obj, PyTypeObject *base, object name,
                          object descr)
    PyObject *simpy_mro_next(object obj, PyTypeObject *cls)
    bint simpy_key3(PyObject *key, double *out)
    PyTypeObject *simpy_type "Py_TYPE" (PyObject *o)
    bint simpy_key3_lt(const double *a, const double *b)
from simpy.core import BoundClass, Environment, SimTime
from simpy.events import Process
from simpy.resources import base

_by_key = _attrgetter('key')


class Preempted:
    """Cause of a preemption :class:`~simpy.exceptions.Interrupt` containing
    information about the preemption.

    """

    def __init__(
        self,
        by: Optional[Process],
        usage_since: Optional[SimTime],
        resource: 'Resource',
    ):
        self.by = by
        """The preempting :class:`simpy.events.Process`."""
        self.usage_since = usage_since
        """The simulation time at which the preempted process started to use
        the resource."""
        self.resource = resource
        """The resource which was lost, i.e., caused the preemption."""


@cython.auto_pickle(False)
cdef class Request(base.Put):
    """Request usage of the *resource*. The event is triggered once access is
    granted. Subclass of :class:`simpy.resources.base.Put`.

    If the maximum capacity of users has not yet been reached, the request is
    triggered immediately. If the maximum capacity has been
    reached, the request is triggered once an earlier usage request on the
    resource is released.

    The request is automatically released when the request was created within
    a :keyword:`with` statement.

    """

    # ``usage_since``: the time at which the request succeeded (``None``
    # until then).

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        super().__exit__(exc_type, exc_value, traceback)
        # Don't release the resource on generator cleanups. This seems to
        # create un-claimable circular references otherwise.
        if exc_type is not GeneratorExit:
            self.resource.release(self)
        return None


@cython.auto_pickle(False)
cdef class Release(base.Get):
    """Releases the usage of *resource* granted by *request*. This event is
    triggered immediately. Subclass of :class:`simpy.resources.base.Get`.

    """

    def __init__(self, resource: 'Resource', request: Request):
        self.request = request
        """The request (:class:`Request`) that is to be released."""
        super().__init__(resource)


@cython.auto_pickle(False)
cdef class PriorityRequest(Request):
    """Request the usage of *resource* with a given *priority*. If the
    *resource* supports preemption and *preempt* is ``True`` other usage
    requests of the *resource* may be preempted (see
    :class:`PreemptiveResource` for details).

    This event type inherits :class:`Request` and adds some additional
    attributes needed by :class:`PriorityResource` and
    :class:`PreemptiveResource`

    """

    def __init__(
        self, resource: 'Resource', priority: int = 0, preempt: bool = True
    ):
        self.priority = priority
        """The priority of this request. A smaller number means higher
        priority."""

        self.preempt = preempt
        """Indicates whether the request should preempt a resource user or not
        (:class:`PriorityResource` ignores this flag)."""

        self.time = env_now(resource._env)
        """The time at which the request was made."""

        self.key = (self.priority, self.time, not self.preempt)
        """Key for sorting events. Consists of the priority (lower value is
        more important), the time at which the request was made (earlier
        requests are more important) and finally the preemption flag (preempt
        requests are more important)."""

        super().__init__(resource)


class SortedQueue(list):
    """Queue for sorting events by their :attr:`~PriorityRequest.key`
    attribute.

    """

    def __init__(self, maxlen: Optional[int] = None):
        super().__init__()
        self.maxlen = maxlen
        """Maximum length of the queue."""

    def append(self, item: Any) -> None:
        """Sort *item* into the queue.

        Raise a :exc:`RuntimeError` if the queue is full.

        """
        if self.maxlen is not None and len(self) >= self.maxlen:
            raise RuntimeError('Cannot append event. Queue is full.')

        if _sorted_insert(self, item):
            return
        super().append(item)
        super().sort(key=_by_key)


cdef int _sorted_insert(object queue, object item) except -1:
    # SortedQueue.append() without the sort, when that is exact: returns 1
    # once *item* is inserted, 0 to leave it to append() and sort().
    #
    # The queue and the new item must be exact PriorityRequests (reading
    # their key runs no code) with keys made of plain numbers (comparing them
    # runs no code and is a total order), and the queue must be sorted. Then
    # the stable sort of the queue plus the item, which upstream computes, is
    # the queue with the item inserted after all equal keys.
    cdef double new[3]
    cdef double cur[3]
    cdef double prev[3]
    cdef Py_ssize_t i, lo, hi, mid, n
    cdef PyObject *ev
    cdef PyTypeObject *tp = <PyTypeObject *>PriorityRequest
    if simpy_mro_next(queue, <PyTypeObject *>_SortedQueue) is not \
            <PyObject *>list:
        return 0  # super() would not find list's append() and sort().
    n = PyList_GET_SIZE(queue)  # queue is a SortedQueue, hence a list.
    if type(item) is not PriorityRequest or not simpy_key3(
            <PyObject *>(<PriorityRequest>item).key, new):
        return 0
    for i in range(n):
        ev = PyList_GET_ITEM(queue, i)
        if simpy_type(ev) != tp or not simpy_key3(
                <PyObject *>(<PriorityRequest>ev).key, cur):
            return 0
        if i and simpy_key3_lt(cur, prev):
            return 0  # Not sorted: let sort() put it in order.
        prev[0], prev[1], prev[2] = cur[0], cur[1], cur[2]
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        simpy_key3(
            <PyObject *>(<PriorityRequest>PyList_GET_ITEM(queue, mid)).key,
            cur)
        if simpy_key3_lt(new, cur):
            hi = mid
        else:
            lo = mid + 1
    PyList_Insert(queue, lo, item)
    return 1


# The class whose append() calls _sorted_insert(): super() in its methods
# refers to it even when the module attribute is rebound.
cdef object _SortedQueue = SortedQueue


cdef class Resource(base.BaseResource):
    """Resource with *capacity* of usage slots that can be requested by
    processes.

    If all slots are taken, requests are enqueued. Once a usage request is
    released, a pending request will be triggered.

    The *env* parameter is the :class:`~simpy.core.Environment` instance the
    resource is bound to.

    """

    def __init__(self, env: Environment, capacity: int = 1):
        if capacity <= 0:
            raise ValueError('"capacity" must be > 0.')

        super().__init__(env, capacity)

        self.users: List[Request] = []
        """List of :class:`Request` events for the processes that are currently
        using the resource."""
        self.queue = self.put_queue
        """Queue of pending :class:`Request` events. Alias of
        :attr:`~simpy.resources.base.BaseResource.put_queue`.
        """

    @property
    def count(self) -> int:
        """Number of users currently using the resource."""
        return len(self.users)

    if TYPE_CHECKING:

        def request(self) -> Request:
            """Request a usage slot."""
            return Request(self)

        def release(self, request: Request) -> Release:
            """Release a usage slot."""
            return Release(self, request)

    else:
        request = BoundClass(Request)
        release = BoundClass(Release)

    cpdef _do_put(self, event):  # type: ignore
        if len(self.users) < _capacity(self):
            self.users.append(event)
            if isinstance(event, Request):
                (<Request>event).usage_since = env_now(self._env)
                (<Request>event).succeed()
            else:
                event.usage_since = env_now(self._env)
                event.succeed()

    cpdef _do_get(self, event):  # type: ignore
        if isinstance(event, Release):
            try:
                self.users.remove((<Release>event).request)
            except ValueError:
                pass
            (<Release>event).succeed()
            return None
        try:
            self.users.remove(event.request)
        except ValueError:
            pass
        event.succeed()


cdef class PriorityResource(Resource):
    """A :class:`~simpy.resources.resource.Resource` supporting prioritized
    requests.

    Pending requests in the :attr:`~Resource.queue` are sorted in ascending
    order by their *priority* (that means lower values are more important).

    """

    PutQueue = SortedQueue
    """Type of the put queue. See
    :attr:`~simpy.resources.base.BaseResource.put_queue` for details."""

    GetQueue = list
    """Type of the get queue. See
    :attr:`~simpy.resources.base.BaseResource.get_queue` for details."""

    def __init__(self, env: Environment, capacity: int = 1):
        super().__init__(env, capacity)

    if TYPE_CHECKING:

        def request(
            self, priority: int = 0, preempt: bool = True
        ) -> PriorityRequest:
            """Request a usage slot with the given *priority*."""
            return PriorityRequest(self, priority, preempt)

        def release(  # type: ignore[override] # noqa: F821
            self, request: PriorityRequest
        ) -> Release:
            """Release a usage slot."""
            return Release(self, request)

    else:
        request = BoundClass(PriorityRequest)
        release = BoundClass(Release)


cdef class PreemptiveResource(PriorityResource):
    """A :class:`~simpy.resources.resource.PriorityResource` with preemption.

    If a request is preempted, the process of that request will receive an
    :class:`~simpy.exceptions.Interrupt` with a :class:`Preempted` instance as
    cause.

    """

    cpdef _do_put(self, event):  # type: ignore
        # Untyped parameter: it must match the cpdef declaration in the .pxd.
        # The event is a PriorityRequest.
        if len(self.users) >= _capacity(self) and event.preempt:
            # Check if we can preempt another process
            preempt = sorted(self.users, key=_by_key)[-1]
            if preempt.key > event.key:
                self.users.remove(preempt)
                preempt.proc.interrupt(  # type: ignore
                    Preempted(
                        by=event.proc,
                        usage_since=preempt.usage_since,
                        resource=self,
                    )
                )

        if type(self) is PreemptiveResource:
            # What super() finds for this exact type.
            return Resource._do_put(self, event)
        # Zero-argument super() is unavailable in cpdef methods.
        return super(PreemptiveResource, self)._do_put(event)


cdef object _CAPACITY = BaseResource.__dict__['capacity']
cdef object _S_CAPACITY = 'capacity'


cdef inline object _capacity(BaseResource res):
    # res.capacity
    if simpy_plain_type(res, <PyTypeObject *>BaseResource, _S_CAPACITY,
                        _CAPACITY):
        return res._capacity
    return res.capacity


register_state_fields(Request, ('usage_since',))
register_state_fields(Release, ('request',))
register_state_fields(PriorityRequest, ('priority', 'preempt', 'time', 'key'))
