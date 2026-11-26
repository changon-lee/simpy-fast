"""Deterministic SimPy workloads for benchmarking.

Each workload takes the ``simpy`` module and returns ``(ops, trace)``. ``ops``
counts the user-visible operations completed. ``trace`` is a list of tuples
recorded at key moments; it is hashed outside the timed region so two SimPy
implementations can be checked for identical event ordering.
"""
import random


def w_timeouts(simpy):
    env = simpy.Environment()
    rng = random.Random(1)
    trace = []

    def proc(i):
        for _ in range(400):
            yield env.timeout(rng.randint(1, 50))
        trace.append((env.now, i))

    for i in range(500):
        env.process(proc(i))
    env.run()
    return 500 * 400, trace


def w_spawn(simpy):
    env = simpy.Environment()
    rng = random.Random(2)
    trace = []

    def child(i):
        yield env.timeout(rng.randint(0, 5))
        return i

    def parent():
        for i in range(100_000):
            p = env.process(child(i))
            if i % 10 == 0:
                v = yield p
                trace.append((env.now, v))
            elif i % 3 == 0:
                yield env.timeout(1)

    env.process(parent())
    env.run()
    return 100_000, trace


def w_resource(simpy):
    env = simpy.Environment()
    rng = random.Random(3)
    res = simpy.Resource(env, capacity=4)
    trace = []

    def user(i):
        for _ in range(60):
            with res.request() as req:
                yield req
                yield env.timeout(rng.randint(1, 3))
            yield env.timeout(rng.randint(0, 2))
        trace.append((env.now, i))

    for i in range(1000):
        env.process(user(i))
    env.run()
    return 1000 * 60, trace


def w_priority_resource(simpy):
    env = simpy.Environment()
    rng = random.Random(4)
    res = simpy.PriorityResource(env, capacity=4)
    trace = []

    def user(i):
        for _ in range(50):
            with res.request(priority=rng.randint(0, 9)) as req:
                yield req
                yield env.timeout(rng.randint(1, 3))
            yield env.timeout(rng.randint(0, 2))
        trace.append((env.now, i))

    for i in range(400):
        env.process(user(i))
    env.run()
    return 400 * 50, trace


def w_container(simpy):
    env = simpy.Environment()
    rng = random.Random(5)
    tank = simpy.Container(env, capacity=100, init=50)
    trace = []

    def producer(i):
        for _ in range(100):
            yield tank.put(rng.randint(1, 5))
            yield env.timeout(rng.randint(1, 4))
        trace.append((env.now, 'p', i, tank.level))

    def consumer(i):
        for _ in range(100):
            yield tank.get(rng.randint(1, 5))
            yield env.timeout(rng.randint(1, 4))
        trace.append((env.now, 'c', i, tank.level))

    for i in range(200):
        env.process(producer(i))
        env.process(consumer(i))
    env.run(until=100_000)
    return 200 * 200, trace


def w_store(simpy):
    env = simpy.Environment()
    rng = random.Random(6)
    stages = [simpy.Store(env, capacity=10) for _ in range(6)]
    trace = []
    n_items = 30_000

    def source():
        for i in range(n_items):
            yield stages[0].put(i)
            if i % 4 == 0:
                yield env.timeout(1)

    def worker(k):
        src, dst = stages[k], stages[k + 1]
        while True:
            item = yield src.get()
            yield env.timeout(rng.randint(1, 6))
            yield dst.put(item)

    def sink():
        last = stages[-1]
        for _ in range(n_items):
            item = yield last.get()
            trace.append((env.now, item))

    env.process(source())
    for k in range(5):
        for _ in range(4):
            env.process(worker(k))
    done = env.process(sink())
    env.run(until=done)
    return n_items * 6, trace


def w_priority_filter_store(simpy):
    env = simpy.Environment()
    rng = random.Random(7)
    pstore = simpy.PriorityStore(env)
    fstore = simpy.FilterStore(env, capacity=50)
    trace = []

    def pprod():
        for i in range(20_000):
            yield pstore.put(simpy.PriorityItem(rng.randint(0, 20), i))
            yield env.timeout(rng.randint(0, 2))

    def pcons(k):
        while True:
            it = yield pstore.get()
            trace.append((env.now, k, it.item))
            yield env.timeout(rng.randint(1, 8))

    def fprod():
        for i in range(10_000):
            yield fstore.put((i % 4, i))
            yield env.timeout(1)

    def fcons(kind):
        while True:
            it = yield fstore.get(lambda x, kind=kind: x[0] == kind)
            trace.append((env.now, 'f', it[1]))
            yield env.timeout(rng.randint(1, 5))

    env.process(pprod())
    for k in range(4):
        env.process(pcons(k))
    env.process(fprod())
    for kind in range(4):
        env.process(fcons(kind))
    env.run(until=60_000)
    return 30_000, trace


def w_conditions(simpy):
    env = simpy.Environment()
    rng = random.Random(8)
    trace = []

    def proc(i):
        for _ in range(30):
            a = env.timeout(rng.randint(1, 10), 'a')
            b = env.timeout(rng.randint(1, 10), 'b')
            res = yield a | b
            trace.append((env.now, i, tuple(res.values())))
            yield env.all_of([env.timeout(rng.randint(1, 5)) for _ in range(3)])

    for i in range(1500):
        env.process(proc(i))
    env.run()
    return 1500 * 30, trace


def w_dse_mix(simpy):
    """A production-line style model: many short-lived job processes, bounded
    buffers (Store), machines (Resource and PriorityResource), shared material
    (Container) with replenishment, and reneging via any_of."""
    env = simpy.Environment()
    rng = random.Random(9)
    n_stations = 6
    machines = [
        (simpy.PriorityResource if k % 2 else simpy.Resource)(env, capacity=2 + k % 3)
        for k in range(n_stations)
    ]
    buffers = [simpy.Store(env, capacity=20) for _ in range(n_stations)]
    material = [simpy.Container(env, capacity=500, init=200) for _ in range(3)]
    trace = []
    stats = {'done': 0, 'reneged': 0}

    def replenish(c):
        while True:
            yield env.timeout(rng.randint(5, 15))
            room = c.capacity - c.level
            if room > 0:
                yield c.put(min(room, rng.randint(20, 60)))

    def job(jid, prio):
        for k in range(n_stations):
            yield buffers[k].put(jid)
            m = machines[k]
            req = m.request(priority=prio) if k % 2 else m.request()
            timeout = env.timeout(200)
            got = yield req | timeout
            if req not in got:
                req.cancel()
                yield buffers[k].get()
                stats['reneged'] += 1
                trace.append((env.now, 'r', jid, k))
                return
            yield buffers[k].get()
            yield material[k % 3].get(rng.randint(1, 4))
            yield env.timeout(rng.randint(1, 5))
            m.release(req)
        stats['done'] += 1
        trace.append((env.now, 'd', jid))

    def arrivals():
        for jid in range(12_000):
            env.process(job(jid, rng.randint(0, 3)))
            yield env.timeout(rng.randint(0, 2))

    for c in material:
        env.process(replenish(c))
    env.process(arrivals())
    env.run(until=40_000)
    trace.append(('stats', stats['done'], stats['reneged']))
    return 12_000 * n_stations, trace


WORKLOADS = {
    'timeouts': w_timeouts,
    'spawn': w_spawn,
    'resource': w_resource,
    'priority_resource': w_priority_resource,
    'container': w_container,
    'store': w_store,
    'prio_filter_store': w_priority_filter_store,
    'conditions': w_conditions,
    'dse_mix': w_dse_mix,
}
