"""Behavior probes at API boundaries; prints one line per probe.

Usage: python bench/compat.py TREE

Run it against upstream and an optimized tree and diff the output: any
difference is a behavior change. It covers inputs the upstream test suite does
not, such as float and numpy priorities and capacities, non-exception fail()
arguments and non-event ConditionValue keys.
"""
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.abspath(sys.argv[1]))
import simpy  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def probe(name, fn):
    try:
        print(f'{name}: ok {fn()!r}')
    except Exception as e:  # noqa: BLE001
        print(f'{name}: {type(e).__name__} {e}')


def priority_order(prios):
    env = simpy.Environment()
    res = simpy.PriorityResource(env, capacity=1)
    order = []

    def user(i, p):
        with res.request(priority=p) as req:
            yield req
            order.append(i)
            yield env.timeout(1)

    for i, p in enumerate(prios):
        env.process(user(i, p))
    env.run()
    return order, [type(p).__name__ for p in prios]


def preempt_flag(flag):
    env = simpy.Environment()
    res = simpy.PreemptiveResource(env, capacity=1)
    req = res.request(priority=1, preempt=flag)
    return req.preempt, req.key


def capacity(cls, cap, **kw):
    env = simpy.Environment()
    r = cls(env, cap, **kw)
    return r.capacity, type(r.capacity).__name__


def store_fill(cap):
    env = simpy.Environment()
    st = simpy.Store(env, capacity=cap)
    for i in range(5):
        st.put(i)
    env.run()
    return len(st.items)


def container_amounts(amount):
    env = simpy.Environment()
    c = simpy.Container(env, capacity=10, init=5)
    g = c.get(amount)
    p = c.put(amount)
    env.run()
    return c.level, g.amount, type(g.amount).__name__, p.triggered


def timeout_delay(delay):
    env = simpy.Environment()
    t = env.timeout(delay)
    env.run()
    return env.now, type(env.now).__name__, t.processed


def condition_value_keys():
    env = simpy.Environment()
    a, b = env.timeout(1, 'a'), env.timeout(2, 'b')
    c = a | b
    env.run(until=c)
    v = c.value
    out = [a in v, b in v]
    for key in ('x', None, 3):
        try:
            out.append((key in v, 'contains'))
        except Exception as e:  # noqa: BLE001
            out.append(type(e).__name__)
        try:
            out.append(v[key])
        except Exception as e:  # noqa: BLE001
            out.append(type(e).__name__)
    return out


def fail_with(value):
    env = simpy.Environment()
    return env.event().fail(value)


def event_with_env(env_obj):
    return simpy.Event(env_obj).env


def release_foreign():
    env = simpy.Environment()
    r1, r2 = simpy.Resource(env), simpy.Resource(env)
    req = r1.request()
    rel = r2.release(req)
    env.run()
    return rel.triggered, r1.count, r2.count


probe('priority int', lambda: priority_order([3, 1, 2, 1]))
probe('priority float', lambda: priority_order([1.5, 1.2, 1.7, 1.2]))
probe('priority bool', lambda: priority_order([True, False, True]))
probe('priority Fraction', lambda: priority_order([Fraction(3, 2), Fraction(1, 2)]))
probe('priority tuple', lambda: priority_order([(1, 2), (1, 1), (0, 9)]))
probe('preempt 0', lambda: preempt_flag(0))
probe('preempt 1', lambda: preempt_flag(1))
probe('preempt None', lambda: preempt_flag(None))
probe('Resource cap float', lambda: capacity(simpy.Resource, 2.5))
probe('Resource cap inf', lambda: capacity(simpy.Resource, float('inf')))
probe('Resource cap bool', lambda: capacity(simpy.Resource, True))
probe('Store cap 2.5', lambda: store_fill(2.5))
probe('Container int', lambda: container_amounts(2))
probe('Container float', lambda: container_amounts(2.5))
probe('Container Fraction', lambda: container_amounts(Fraction(5, 2)))
probe('timeout int', lambda: timeout_delay(3))
probe('timeout float', lambda: timeout_delay(2.5))
probe('timeout Fraction', lambda: timeout_delay(Fraction(1, 3)))
probe('timeout negative', lambda: timeout_delay(-1))
probe('ConditionValue keys', condition_value_keys)
probe('fail str', lambda: fail_with('x'))
probe('fail KeyboardInterrupt', lambda: type(fail_with(KeyboardInterrupt())).__name__)
probe('Event(None)', lambda: event_with_env(None))
probe('release foreign request', release_foreign)
if np is not None:
    probe('priority numpy int64', lambda: priority_order(list(np.array([3, 1, 2], dtype=np.int64))))
    probe('priority numpy float', lambda: priority_order(list(np.array([1.5, 1.2], dtype=np.float64))))
    probe('Resource cap numpy', lambda: capacity(simpy.Resource, np.int64(3)))
    probe('Container numpy amount', lambda: container_amounts(np.float64(2.5)))
    probe('timeout numpy', lambda: timeout_delay(np.float64(1.5)))
