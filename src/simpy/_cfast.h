/* Small C helpers shared by SimPy's compiled modules. */
#ifndef SIMPY_CFAST_H
#define SIMPY_CFAST_H

#include <Python.h>
#include <math.h>

/* 1 when `obj.<name>` resolves to `func`, the function stored under `name`
 * in the dict of the extension type `base` (obj is an instance of base or of
 * a subclass): no subclass overrides it, the instance dict does not shadow it
 * and attribute access is the generic one. Calling the C implementation
 * directly is then exactly what `obj.<name>(...)` does. 0 means "take the
 * Python attribute route", which is always correct. */
static inline int simpy_plain(PyObject *obj, PyTypeObject *base,
                              PyObject *name, PyObject *func)
{
    PyTypeObject *tp = Py_TYPE(obj);
    Py_ssize_t off;
    PyObject *dict;

    if (tp != base) {
        if (tp->tp_getattro != base->tp_getattro)
            return 0;
        if (_PyType_Lookup(tp, name) != func)
            return 0;
#ifdef Py_TPFLAGS_MANAGED_DICT
        if (tp->tp_flags & Py_TPFLAGS_MANAGED_DICT)
            return 0;
#endif
    }
    off = tp->tp_dictoffset;
    if (off == 0)
        return 1;
    if (off < 0)
        return 0;
    dict = *(PyObject **)((char *)obj + off);
    if (dict == NULL || PyDict_GET_SIZE(dict) == 0)
        return 1;
    if (PyDict_GetItemWithError(dict, name) != NULL)
        return 0;
    if (PyErr_Occurred()) {
        /* The attribute route raises it again. */
        PyErr_Clear();
        return 0;
    }
    return 1;
}

/* 1 when `obj.<name>` is the data descriptor `descr` (a property or C field
 * of the extension type `base`): obj is an instance of base or of a subclass
 * that overrides neither <name> nor attribute access. A data descriptor takes
 * precedence over the instance dict, which therefore does not matter. */
static inline int simpy_plain_type(PyObject *obj, PyTypeObject *base,
                                   PyObject *name, PyObject *descr)
{
    PyTypeObject *tp = Py_TYPE(obj);

    if (tp == base)
        return 1;
    if (!PyType_IsSubtype(tp, base) || tp->tp_getattro != base->tp_getattro)
        return 0;
    return _PyType_Lookup(tp, name) == descr;
}

/* 1 when obj has no instance attribute `name`, decided without running
 * Python code: it has no instance dict, or a small exact dict whose keys are
 * all exact str and none equals `name`. 0 when it may have one. */
static inline int simpy_no_instance_attr(PyObject *obj, PyObject *name)
{
    PyTypeObject *tp = Py_TYPE(obj);
    Py_ssize_t off, len, pos = 0;
    PyObject *dict, *key, *value;

#ifdef Py_TPFLAGS_MANAGED_DICT
    if (tp->tp_flags & Py_TPFLAGS_MANAGED_DICT)
        return 0;
#endif
    off = tp->tp_dictoffset;
    if (off == 0)
        return 1;
    if (off < 0)
        return 0;
    dict = *(PyObject **)((char *)obj + off);
    if (dict == NULL)
        return 1;
    if (!PyDict_CheckExact(dict) || PyDict_GET_SIZE(dict) > 8)
        return 0;
    len = PyUnicode_GET_LENGTH(name);
    while (PyDict_Next(dict, &pos, &key, &value)) {
        if (key == name || !PyUnicode_CheckExact(key))
            return 0;
        if (PyUnicode_GET_LENGTH(key) == len && PyUnicode_Compare(key, name) == 0)
            return 0;
    }
    return 1;
}

/* For Cython's cpdef methods (the build inserts the call, see
 * cython_config.py). When C code calls one, Cython looks up
 * getattr(obj, name), which allocates a bound method, and runs the C body
 * only if that is a method of the cpdef wrapper, whose C function is
 * `cfunc`. This proves the same without the lookup: attribute access is the
 * generic one, the class attribute found is that wrapper, a Cython function
 * (`cyfunc_type`, which getattr binds to obj), and no instance attribute
 * hides it. 0 leaves the decision to Cython's own check. */
static inline int simpy_cpdef_plain(PyObject *obj, PyObject *name,
                                    void (*cfunc)(void),
                                    PyTypeObject *cyfunc_type)
{
    PyTypeObject *tp = Py_TYPE(obj);
    PyObject *descr;

    if (tp->tp_getattro != PyBaseObject_Type.tp_getattro)
        return 0;
    descr = _PyType_Lookup(tp, name);
    if (descr == NULL || Py_TYPE(descr) != cyfunc_type ||
            ((PyCFunctionObject *)descr)->m_ml->ml_meth != (PyCFunction)cfunc)
        return 0;
    return simpy_no_instance_attr(obj, name);
}

/* The class that follows `cls` in the MRO of type(obj) (borrowed), or NULL.
 * super(cls, obj).<name> finds the first class after `cls` that defines
 * <name>; when that next class is a compiled (immutable) type which defines
 * it, the result is known without asking super(). */
static inline PyObject *simpy_mro_next(PyObject *obj, PyTypeObject *cls)
{
    PyObject *mro = Py_TYPE(obj)->tp_mro;
    Py_ssize_t i, n;

    if (mro == NULL || !PyTuple_Check(mro))
        return NULL;
    n = PyTuple_GET_SIZE(mro);
    for (i = 0; i + 1 < n; i++) {
        if (PyTuple_GET_ITEM(mro, i) == (PyObject *)cls)
            return PyTuple_GET_ITEM(mro, i + 1);
    }
    return NULL;
}

/* A number that compares exactly like a double and without running Python
 * code: an exact float other than NaN, or an exact int with |v| <= 2**53.
 * 1 and *out set when `o` is one, else 0. */
/* The value of the exact int `o`: 1 and *out set when it fits a long long,
 * else 0. */
static inline int simpy_long_value(PyObject *o, long long *out)
{
    int overflow;
    long long v;

#if PY_VERSION_HEX >= 0x030C0000
    if (PyUnstable_Long_IsCompact((PyLongObject *)o)) {
        *out = (long long)PyUnstable_Long_CompactValue((PyLongObject *)o);
        return 1;
    }
#endif
    v = PyLong_AsLongLongAndOverflow(o, &overflow);
    if (overflow)
        return 0;
    *out = v;
    return 1;
}

static inline int simpy_time_key(PyObject *o, double *out)
{
    long long v;

    if (PyFloat_CheckExact(o)) {
        double d = PyFloat_AS_DOUBLE(o);
        if (d != d)
            return 0;  /* NaN: tuple comparison treats it by identity */
        *out = d;
        return 1;
    }
    if (PyLong_CheckExact(o) && simpy_long_value(o, &v) &&
            v <= 9007199254740992LL && v >= -9007199254740992LL) {
        *out = (double)v;  /* exact: |v| <= 2**53 */
        return 1;
    }
    return 0;
}

/* A PriorityRequest key that compares like three doubles: an exact 3-tuple
 * of such numbers or bools. Comparing two of them with `<` runs no Python
 * code and gives what comparing the doubles lexicographically gives. */
static inline int simpy_key3(PyObject *key, double *out)
{
    Py_ssize_t i;

    if (!PyTuple_CheckExact(key) || PyTuple_GET_SIZE(key) != 3)
        return 0;
    for (i = 0; i < 3; i++) {
        PyObject *v = PyTuple_GET_ITEM(key, i);
        if (v == Py_True)
            out[i] = 1.0;
        else if (v == Py_False)
            out[i] = 0.0;
        else if (!simpy_time_key(v, out + i))
            return 0;
    }
    return 1;
}

static inline int simpy_key3_lt(const double *a, const double *b)
{
    if (a[0] != b[0])
        return a[0] < b[0];
    if (a[1] != b[1])
        return a[1] < b[1];
    return a[2] < b[2];
}

/* list.pop(i) for an exact list, as a new reference. The list ends up as
 * the method leaves it: same items, and the same allocation, since both
 * shrink through list_resize() or clear the list when it becomes empty.
 * Before 3.11, pop() empties a list through list_resize(), which keeps an
 * allocation of one slot; the method handles that case and out of range
 * indices. */
static inline PyObject *simpy_list_pop(PyObject *list, Py_ssize_t i)
{
    Py_ssize_t n = PyList_GET_SIZE(list);
    PyObject *item;

    if (i < 0 || i >= n || (n == 1 && ((PyListObject *)list)->allocated == 1))
        return PyObject_CallMethod(list, "pop", "n", i);
    item = PyList_GET_ITEM(list, i);
    Py_INCREF(item);
    if (PyList_SetSlice(list, i, i + 1, NULL) < 0) {
        Py_DECREF(item);
        return NULL;
    }
    return item;
}

/* `a != b` as a truth value, -1 on error. When both types keep object's
 * rich comparison, Python answers by identity. */
static inline int simpy_ne(PyObject *a, PyObject *b)
{
    richcmpfunc base = PyBaseObject_Type.tp_richcompare;
    PyObject *r;
    int t;

    if (Py_TYPE(a)->tp_richcompare == base &&
            Py_TYPE(b)->tp_richcompare == base)
        return a != b;
    r = PyObject_RichCompare(a, b, Py_NE);
    if (r == NULL)
        return -1;
    t = PyObject_IsTrue(r);
    Py_DECREF(r);
    return t;
}

/* `now + 0` as upstream computes it when scheduling without delay, as a new
 * reference. For an exact int or float (other than -0.0) the sum equals
 * `now` in value and type, so `now` itself is returned. */
static inline PyObject *simpy_add_zero(PyObject *now, PyObject *zero)
{
    if (PyLong_CheckExact(now) ||
            (PyFloat_CheckExact(now) &&
             !(PyFloat_AS_DOUBLE(now) == 0.0 &&
               copysign(1.0, PyFloat_AS_DOUBLE(now)) < 0.0))) {
        Py_INCREF(now);
        return now;
    }
    return PyNumber_Add(now, zero);
}

/* gen.send(arg) for Process._resume, as a new reference. *returned is set to
 * 1 when the generator returned (the result is its return value, which
 * upstream reads from StopIteration.args), else 0. NULL with an exception
 * set when the call raised. Exact generators go through am_send, which is
 * what gen.send() runs, minus raising and catching StopIteration on return;
 * any other object gets a plain .send() call. */
static inline PyObject *simpy_send(PyObject *gen, PyObject *arg,
                                   PyObject *send_name, int *returned)
{
#if PY_VERSION_HEX >= 0x030A0000
    if (PyGen_CheckExact(gen)) {
        PyObject *res = NULL;
        PySendResult r = PyIter_Send(gen, arg, &res);
        *returned = (r == PYGEN_RETURN);
        return r == PYGEN_ERROR ? NULL : res;
    }
#endif
    *returned = 0;
    return PyObject_CallMethodOneArg(gen, send_name, arg);
}

#endif /* SIMPY_CFAST_H */
