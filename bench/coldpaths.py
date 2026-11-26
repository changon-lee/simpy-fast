"""Workloads on features the benchmark workloads do not use: preemption,
interrupts, filtered gets, nested conditions, failing events, float
containers. Used to check that a build tuned on bench/workloads.py does not
slow these paths down.

Usage: python bench/coldpaths.py TREE [ROUNDS]
Each workload takes the ``simpy`` module, like those in workloads.py.
"""
import os
import random
import sys
import time


def w_preempt(simpy):
    env = simpy.Environment()
    rng = random.Random(1)
    res = simpy.PreemptiveResource(env, capacity=2)
    done = [0]

    def user(i):
        for _ in range(40):
            with res.request(priority=rng.randint(0, 5),
                             preempt=rng.random() < 0.5) as req:
                try:
                    yield req
                    yield env.timeout(rng.randint(1, 4))
                    done[0] += 1
                except simpy.Interrupt:
                    pass
            yield env.timeout(rng.randint(0, 2))

    for i in range(150):
        env.process(user(i))
    env.run()
    return done[0]


def w_interrupts(simpy):
    env = simpy.Environment()
    rng = random.Random(2)
    n = [0]

    def worker():
        while True:
            try:
                yield env.timeout(rng.randint(5, 20))
            except simpy.Interrupt as i:
                n[0] += i.cause

    def disturber(procs):
        for _ in range(20_000):
            yield env.timeout(1)
            procs[rng.randrange(len(procs))].interrupt(1)

    procs = [env.process(worker()) for _ in range(50)]
    env.process(disturber(procs))
    env.run(until=20_001)
    return n[0]


def w_filter_store(simpy):
    env = simpy.Environment()
    rng = random.Random(3)
    st = simpy.FilterStore(env, capacity=30)
    got = [0]

    def prod():
        for i in range(30_000):
            yield st.put((i % 7, i))
            yield env.timeout(rng.randint(0, 1))

    def cons(k):
        while True:
            yield st.get(lambda x, k=k: x[0] == k)
            got[0] += 1

    env.process(prod())
    for k in range(7):
        env.process(cons(k))
    env.run(until=40_000)
    return got[0]


def w_nested_conditions(simpy):
    env = simpy.Environment()
    rng = random.Random(4)
    n = [0]

    def proc():
        for _ in range(200):
            a, b, c = (env.timeout(rng.randint(1, 9)) for _ in range(3))
            res = yield (a & b) | c
            n[0] += len(res.todict())

    for _ in range(300):
        env.process(proc())
    env.run()
    return n[0]


def w_failures(simpy):
    env = simpy.Environment()
    n = [0]

    def child(i):
        yield env.timeout(1)
        if i % 2:
            raise ValueError(i)
        return i

    def parent():
        for i in range(30_000):
            try:
                yield env.process(child(i))
            except ValueError:
                n[0] += 1

    env.process(parent())
    env.run()
    return n[0]


def w_float_container(simpy):
    env = simpy.Environment()
    rng = random.Random(5)
    c = simpy.Container(env, capacity=100.0, init=50.0)

    def prod():
        for _ in range(20_000):
            yield c.put(rng.random() * 3 + 0.1)
            yield env.timeout(0.5)

    def cons():
        for _ in range(20_000):
            yield c.get(rng.random() * 3 + 0.1)
            yield env.timeout(0.5)

    env.process(prod())
    env.process(cons())
    env.run()
    return round(c.level, 6)


WORKLOADS = [w_preempt, w_interrupts, w_filter_store, w_nested_conditions,
             w_failures, w_float_container]

if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath(sys.argv[1]))
    import simpy
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    total = 0.0
    for w in WORKLOADS:
        best = float('inf')
        for _ in range(rounds):
            t = time.perf_counter()
            result = w(simpy)
            best = min(best, time.perf_counter() - t)
        total += best
        print(f'{w.__name__[2:]:<20}{best:8.3f}s  result={result}')
    print(f'{"TOTAL":<20}{total:8.3f}s')
