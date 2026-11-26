"""Reference-leak check for the compiled modules.

Usage: python bench/leakcheck.py TREE

Runs small models whose events carry sentinel objects (values, times,
items, return values) and checks that every sentinel's reference count is
back to its baseline once the environment is gone, then that repeating a
model does not grow the number of live objects.
"""
import gc
import os
import sys

sys.path.insert(0, os.path.abspath(sys.argv[1]))
import simpy  # noqa: E402


def model(val, t0):
    env = simpy.Environment(t0)
    res = simpy.Resource(env, 1)
    store = simpy.Store(env, 2)
    cont = simpy.Container(env, 10, init=5)

    def worker(i):
        for _ in range(3):
            with res.request() as req:
                yield req
                yield env.timeout(1, val)
            yield store.put(val)
            got = yield store.get()
            assert got is val
            yield cont.get(1)
            yield cont.put(1)
            yield env.timeout(0.5) | env.timeout(1, val)
            yield env.all_of([env.timeout(1, val), env.timeout(2)])
        return val

    def interrupter(p):
        yield env.timeout(2)
        if p.is_alive:
            p.interrupt(val)

    def victim():
        try:
            yield env.timeout(100, val)
        except simpy.Interrupt as e:
            assert e.cause is val
        return val

    procs = [env.process(worker(i)) for i in range(4)]
    v = env.process(victim())
    env.process(interrupter(v))
    env.run(until=env.all_of(procs + [v]))
    ev = env.event()
    ev.fail(ValueError(val))
    ev.defused = True
    env.run()
    try:
        env.step()
    except simpy.core.EmptySchedule:
        pass
    _ = env._queue  # Switch to the list-based queue and keep running.
    env.timeout(1, val)
    env.run()


def main():
    val = object()
    t0 = float(10 ** 6)  # A float object the heap holds as event time.
    model(val, t0)  # Warm-up: caches, interned objects, type attributes.
    gc.collect()
    base_val, base_t0 = sys.getrefcount(val), sys.getrefcount(t0)
    for _ in range(50):
        model(val, t0)
    gc.collect()
    leaked = (sys.getrefcount(val) - base_val, sys.getrefcount(t0) - base_t0)
    print('sentinel refcount delta (value, time):', leaked)

    n0 = len(gc.get_objects())
    for _ in range(200):
        model(val, t0)
    gc.collect()
    grown = len(gc.get_objects()) - n0
    print('live object growth after 200 runs:', grown)
    ok = leaked == (0, 0) and grown < 50
    print('LEAKCHECK:', 'OK' if ok else 'LEAK')
    sys.exit(0 if ok else 1)


main()
