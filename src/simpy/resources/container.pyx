"""
Resource for sharing homogeneous matter between processes, either continuous
(like water) or discrete (like apples).

A :class:`Container` can be used to model the fuel tank of a gasoline station.
Tankers increase and refuelled cars decrease the amount of gas in the station's
fuel tanks.

"""
from typing import TYPE_CHECKING, Optional, Union

cimport cython

from cpython.object cimport PyObject, PyTypeObject

from simpy.events cimport register_state_fields

from simpy.resources.base cimport get_init, put_init

from simpy.core cimport SimpyAttrWatch, attr_watch

cdef extern from "_cfast.h":
    void simpy_attr_watch_use(SimpyAttrWatch *w)
    PyObject *simpy_mro_next(object obj, PyTypeObject *cls)
from simpy.core import BoundClass, Environment
from simpy.resources import base

# The attribute cache of resources (see _cfast.h).
simpy_attr_watch_use(attr_watch())


ContainerAmount = Union[int, float]


@cython.auto_pickle(False)
cdef class ContainerPut(base.Put):
    """Request to put *amount* of matter into the *container*. The request will
    be triggered once there is enough space in the *container* available.

    Raise a :exc:`ValueError` if ``amount <= 0``.

    """

    def __init__(self, container: 'Container', amount: ContainerAmount):
        if amount <= 0:
            raise ValueError(f'amount(={amount}) must be > 0.')
        self.amount = amount
        """The amount of matter to be put into the container."""

        if simpy_mro_next(self, <PyTypeObject *>ContainerPut) is <PyObject *>base.Put:
            put_init(self, container)  # super().__init__(container)
        else:
            super(ContainerPut, self).__init__(container)


@cython.auto_pickle(False)
cdef class ContainerGet(base.Get):
    """Request to get *amount* of matter from the *container*. The request will
    be triggered once there is enough matter available in the *container*.

    Raise a :exc:`ValueError` if ``amount <= 0``.

    """

    def __init__(self, container: 'Container', amount: ContainerAmount):
        if amount <= 0:
            raise ValueError(f'amount(={amount}) must be > 0.')
        self.amount = amount
        """The amount of matter to be taken out of the container."""

        if simpy_mro_next(self, <PyTypeObject *>ContainerGet) is <PyObject *>base.Get:
            get_init(self, container)  # super().__init__(container)
        else:
            super(ContainerGet, self).__init__(container)


cdef class Container(base.BaseResource):
    """Resource containing up to *capacity* of matter which may either be
    continuous (like water) or discrete (like apples). It supports requests to
    put or get matter into/from the container.

    The *env* parameter is the :class:`~simpy.core.Environment` instance the
    container is bound to.

    The *capacity* defines the size of the container. By default, a container
    is of unlimited size. The initial amount of matter is specified by *init*
    and defaults to ``0``.

    Raise a :exc:`ValueError` if ``capacity <= 0``, ``init < 0`` or
    ``init > capacity``.

    """

    def __init__(
        self,
        env: Environment,
        capacity: ContainerAmount = float('inf'),
        init: ContainerAmount = 0,
    ):
        if capacity <= 0:
            raise ValueError('"capacity" must be > 0.')
        if init < 0:
            raise ValueError('"init" must be >= 0.')
        if init > capacity:
            raise ValueError('"init" must be <= "capacity".')

        super().__init__(env, capacity)

        self._level = init

    @property
    def level(self) -> ContainerAmount:
        """The current amount of the matter in the container."""
        return self._level

    if TYPE_CHECKING:

        def put(  # type: ignore[override] # noqa: F821
            self, amount: ContainerAmount
        ) -> ContainerPut:
            """Request to put *amount* of matter into the container."""
            return ContainerPut(self, amount)

        def get(  # type: ignore[override] # noqa: F821
            self, amount: ContainerAmount
        ) -> ContainerGet:
            """Request to get *amount* of matter out of the container."""
            return ContainerGet(self, amount)

    else:
        put = BoundClass(ContainerPut)
        get = BoundClass(ContainerGet)

    cpdef _do_put(self, event):  # type: ignore
        cdef ContainerPut ev
        if not isinstance(event, ContainerPut):
            if self._capacity - self._level >= event.amount:
                self._level += event.amount
                event.succeed()
                return True
            return None
        ev = <ContainerPut>event
        if self._capacity - self._level >= ev.amount:
            self._level += ev.amount
            ev.succeed()
            return True
        else:
            return None

    cpdef _do_get(self, event):  # type: ignore
        cdef ContainerGet ev
        if not isinstance(event, ContainerGet):
            if self._level >= event.amount:
                self._level -= event.amount
                event.succeed()
                return True
            return None
        ev = <ContainerGet>event
        if self._level >= ev.amount:
            self._level -= ev.amount
            ev.succeed()
            return True
        else:
            return None


register_state_fields(ContainerPut, ('amount',))
register_state_fields(ContainerGet, ('amount',))
