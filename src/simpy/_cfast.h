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

#endif /* SIMPY_CFAST_H */
