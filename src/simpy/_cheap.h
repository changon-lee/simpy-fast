/* Event queue of simpy.core.Environment: a binary heap of C structs.
 *
 * Upstream keeps (time, priority, eid, event) tuples in a list managed by
 * heapq. Environment uses this heap instead while every time is an exact
 * float (not NaN) or an exact int with |t| <= 2**53 and every priority an
 * exact int that fits a long long. For such keys, comparing (t, prio, eid)
 * as C numbers answers exactly what comparing the tuples answers, and the
 * sift loops below follow Modules/_heapqmodule.c step by step, so the heap
 * layout, and with it the pop order, is the same as upstream's list.
 * simpy_heap_to_list() rebuilds that list when Environment has to fall back
 * to it (a key that does not qualify, or Python code using env._queue).
 */
#ifndef SIMPY_CHEAP_H
#define SIMPY_CHEAP_H

#include <Python.h>

typedef struct {
    double t;
    long long prio;
    unsigned long long eid;
    PyObject *time;   /* strong reference */
    PyObject *event;  /* strong reference */
} SimpyQEntry;

typedef struct {
    PyObject_HEAD
    SimpyQEntry *a;
    Py_ssize_t n;
    Py_ssize_t cap;
} SimpyHeap;

#define SIMPY_HEAP(o) ((SimpyHeap *)(o))

static int simpy_heap_traverse(PyObject *self, visitproc visit, void *arg)
{
    SimpyHeap *h = SIMPY_HEAP(self);
    for (Py_ssize_t i = 0; i < h->n; i++) {
        Py_VISIT(h->a[i].time);
        Py_VISIT(h->a[i].event);
    }
    return 0;
}

/* Detach the entries before releasing them: a release may run code that
 * pushes onto this heap again. */
static void simpy_heap_drop(SimpyHeap *h)
{
    SimpyQEntry *a = h->a;
    Py_ssize_t n = h->n;
    h->a = NULL;
    h->n = 0;
    h->cap = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        Py_DECREF(a[i].time);
        Py_DECREF(a[i].event);
    }
    PyMem_Free(a);
}

static int simpy_heap_clear(PyObject *self)
{
    simpy_heap_drop(SIMPY_HEAP(self));
    return 0;
}

static void simpy_heap_dealloc(PyObject *self)
{
    PyObject_GC_UnTrack(self);
    simpy_heap_drop(SIMPY_HEAP(self));
    PyObject_GC_Del(self);
}

static PyTypeObject SimpyHeap_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "simpy.core._EventHeap",
    .tp_basicsize = sizeof(SimpyHeap),
    .tp_dealloc = simpy_heap_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .tp_traverse = simpy_heap_traverse,
    .tp_clear = simpy_heap_clear,
};

static int simpy_heap_ready(void)
{
    return PyType_Ready(&SimpyHeap_Type);
}

static PyObject *simpy_heap_new(void)
{
    SimpyHeap *h = PyObject_GC_New(SimpyHeap, &SimpyHeap_Type);
    if (h == NULL)
        return NULL;
    h->a = NULL;
    h->n = 0;
    h->cap = 0;
    PyObject_GC_Track((PyObject *)h);
    return (PyObject *)h;
}

static inline Py_ssize_t simpy_heap_len(PyObject *self)
{
    return SIMPY_HEAP(self)->n;
}

/* Borrowed reference to the time of the first entry; requires n > 0. */
static inline PyObject *simpy_heap_top_time(PyObject *self)
{
    return SIMPY_HEAP(self)->a[0].time;
}

/* Key of a time object: 1 and *out set when it qualifies, else 0. */
static inline int simpy_time_key(PyObject *o, double *out)
{
    if (PyFloat_CheckExact(o)) {
        double d = PyFloat_AS_DOUBLE(o);
        if (d != d)
            return 0;  /* NaN: tuple comparison treats it by identity */
        *out = d;
        return 1;
    }
    if (PyLong_CheckExact(o)) {
        int overflow;
        long long v = PyLong_AsLongLongAndOverflow(o, &overflow);
        if (overflow || v > 9007199254740992LL || v < -9007199254740992LL)
            return 0;  /* not exactly representable as a double */
        *out = (double)v;
        return 1;
    }
    return 0;
}

static inline int simpy_prio_key(PyObject *o, long long *out)
{
    if (PyLong_CheckExact(o)) {
        int overflow;
        long long v = PyLong_AsLongLongAndOverflow(o, &overflow);
        if (overflow)
            return 0;
        *out = v;
        return 1;
    }
    return 0;
}

/* (t, prio, eid) < (t, prio, eid), the tuple comparison for these keys. */
static inline int simpy_heap_lt(const SimpyQEntry *x, const SimpyQEntry *y)
{
    if (x->t != y->t)
        return x->t < y->t;
    if (x->prio != y->prio)
        return x->prio < y->prio;
    return x->eid < y->eid;
}

/* heapq.heappush(heap, (time, prio, eid, event)) */
static int simpy_heap_push(PyObject *self, double t, long long prio,
                           unsigned long long eid, PyObject *time,
                           PyObject *event)
{
    SimpyHeap *h = SIMPY_HEAP(self);
    SimpyQEntry *a;
    SimpyQEntry item;
    Py_ssize_t pos, parent;

    if (h->n == h->cap) {
        Py_ssize_t cap = h->cap ? h->cap * 2 : 64;
        a = (SimpyQEntry *)PyMem_Realloc(h->a, cap * sizeof(SimpyQEntry));
        if (a == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        h->a = a;
        h->cap = cap;
    }
    a = h->a;
    item.t = t;
    item.prio = prio;
    item.eid = eid;
    Py_INCREF(time);
    item.time = time;
    Py_INCREF(event);
    item.event = event;
    /* _siftdown(heap, 0, len(heap) - 1) */
    pos = h->n++;
    while (pos > 0) {
        parent = (pos - 1) >> 1;
        if (!simpy_heap_lt(&item, &a[parent]))
            break;
        a[pos] = a[parent];
        pos = parent;
    }
    a[pos] = item;
    return 0;
}

/* heapq.heappop(heap) into *out; the caller owns out->time and out->event.
 * Requires n > 0. */
static inline void simpy_heap_pop(PyObject *self, SimpyQEntry *out)
{
    SimpyHeap *h = SIMPY_HEAP(self);
    SimpyQEntry *a = h->a;
    Py_ssize_t n = --h->n, pos = 0, limit, child, parent;
    SimpyQEntry last = a[n];

    if (n == 0) {
        *out = last;
        return;
    }
    *out = a[0];
    /* heap[0] = last; _siftup(heap, 0): move the smaller child up until a
     * leaf is reached ... */
    limit = n >> 1;
    while (pos < limit) {
        child = 2 * pos + 1;
        if (child + 1 < n && !simpy_heap_lt(&a[child], &a[child + 1]))
            child++;
        a[pos] = a[child];
        pos = child;
    }
    /* ... then _siftdown(heap, 0, pos) with `last` as the new item. */
    while (pos > 0) {
        parent = (pos - 1) >> 1;
        if (!simpy_heap_lt(&last, &a[parent]))
            break;
        a[pos] = a[parent];
        pos = parent;
    }
    a[pos] = last;
}

/* The list upstream would hold: [(time, priority, eid, event), ...] in heap
 * order. */
static PyObject *simpy_heap_to_list(PyObject *self)
{
    SimpyHeap *h = SIMPY_HEAP(self);
    PyObject *lst = PyList_New(h->n);
    if (lst == NULL)
        return NULL;
    for (Py_ssize_t i = 0; i < h->n; i++) {
        SimpyQEntry *e = &h->a[i];
        PyObject *prio = PyLong_FromLongLong(e->prio);
        PyObject *eid = prio ? PyLong_FromUnsignedLongLong(e->eid) : NULL;
        PyObject *tup = eid ? PyTuple_Pack(4, e->time, prio, eid, e->event)
                            : NULL;
        Py_XDECREF(prio);
        Py_XDECREF(eid);
        if (tup == NULL) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, tup);
    }
    return lst;
}

#endif /* SIMPY_CHEAP_H */
