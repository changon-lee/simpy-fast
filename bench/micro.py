"""Per-operation cost in ns for one SimPy tree: python bench/micro.py TREE

Each case runs N iterations of one pattern inside a single process, so the
number is (library + minimal generator overhead) per iteration.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(sys.argv[1]))
import simpy  # noqa: E402

N = 200_000


def case_timeout(env):
    def p():
        for _ in range(N):
            yield env.timeout(1)
    env.process(p())


def case_event_succeed(env):
    def p():
        for _ in range(N):
            ev = env.event()
            ev.succeed(1)
            yield ev
    env.process(p())


def case_spawn(env):
    def child():
        yield env.timeout(0)

    def p():
        for _ in range(N):
            yield env.process(child())
    env.process(p())


def case_resource(env):
    res = simpy.Resource(env, 1)

    def p():
        for _ in range(N):
            with res.request() as req:
                yield req
    env.process(p())


def case_priority_resource(env):
    res = simpy.PriorityResource(env, 1)

    def p():
        for _ in range(N):
            with res.request(priority=1) as req:
                yield req
    env.process(p())


def case_store(env):
    st = simpy.Store(env)

    def p():
        for i in range(N):
            yield st.put(i)
            yield st.get()
    env.process(p())


def case_container(env):
    c = simpy.Container(env, init=10)

    def p():
        for _ in range(N):
            yield c.put(1)
            yield c.get(1)
    env.process(p())


def case_any_of(env):
    def p():
        for _ in range(N):
            yield env.timeout(1) | env.timeout(2)
    env.process(p())


def case_all_of(env):
    def p():
        for _ in range(N):
            yield env.timeout(1) & env.timeout(1)
    env.process(p())


CASES = [v for k, v in sorted(globals().items()) if k.startswith('case_')]
for case in CASES:
    best = float('inf')
    for _ in range(3):
        env = simpy.Environment()
        case(env)
        t0 = time.perf_counter()
        env.run()
        best = min(best, time.perf_counter() - t0)
    print(f'{case.__name__[5:]:<20}{best / N * 1e9:8.0f} ns')
