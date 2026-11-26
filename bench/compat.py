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


def mixed_model(env, log):
    """A small model with timeouts, processes, resources and conditions whose
    trace depends on the exact event order."""
    res = simpy.Resource(env, capacity=1)
    store = simpy.Store(env, capacity=2)

    def worker(i, delays):
        for d in delays:
            with res.request() as req:
                yield req
                yield env.timeout(d)
            yield store.put((i, env.now))
            got = yield env.timeout(1, 'a') | env.timeout(1, 'b')
            log.append((i, env.now, sorted(got.values())))

    def consumer():
        while True:
            item = yield store.get()
            log.append(('c', env.now, item))

    env.process(consumer())
    for i, delays in enumerate([[1, 2.5, 0], [0, 0, 3], [2, 0.5, 1]]):
        env.process(worker(i, delays))


def schedule_override():
    calls = []

    class Env(simpy.Environment):
        def schedule(self, event, *args):
            calls.append((type(event).__name__, args))
            super().schedule(event, *args)

    env = Env()
    log = []
    mixed_model(env, log)
    env.run(until=20)
    return log, calls


def schedule_patched():
    env = simpy.Environment()
    calls = []
    orig = env.schedule

    def schedule(event, *args):
        calls.append((type(event).__name__, args))
        orig(event, *args)

    env.schedule = schedule
    log = []
    mixed_model(env, log)
    env.run(until=20)
    return log, calls


def queue_view(env):
    return [(t, p, e, type(ev).__name__) for t, p, e, ev in env._queue]


def queue_access_midrun(at):
    env = simpy.Environment()
    log = []
    mixed_model(env, log)
    env.run(until=at)
    view = queue_view(env)
    env.run(until=20)
    return view, log, env.peek()


def monitored_step():
    # The step-patching recipe of SimPy's monitoring guide; it reads _queue.
    from functools import wraps
    env = simpy.Environment()
    seen = []
    step = env.step

    @wraps(step)
    def tracing_step():
        if len(env._queue):
            t, prio, eid, event = env._queue[0]
            seen.append((t, prio, eid, type(event).__name__))
        return step()

    env.step = tracing_step
    log = []
    mixed_model(env, log)
    env.run(until=20)
    return seen, log


def eid_access():
    env = simpy.Environment()
    log = []
    mixed_model(env, log)
    env.run(until=3)
    first = next(env._eid)
    env.run(until=20)
    return first, log, queue_view(env)


def time_keys(initial, delays):
    env = simpy.Environment(initial)
    out = []

    def p(i, d):
        yield env.timeout(d)
        out.append((i, repr(env.now), type(env.now).__name__))
        yield env.timeout(0)
        out.append((i, repr(env.now)))

    for i, d in enumerate(delays):
        env.process(p(i, d))
    peeks = []
    while env.peek() != simpy.core.Infinity:
        peeks.append(repr(env.peek()))
        env.step()
    return out, peeks


def schedule_priorities(prios):
    env = simpy.Environment()
    order = []
    for i, prio in enumerate(prios):
        ev = env.event()
        ev._ok = True
        ev._value = i
        ev.callbacks.append(lambda e: order.append(e._value))
        env.schedule(ev, prio, (i % 2) * 0.5)
    env.run()
    return order


def process_returns():
    env = simpy.Environment()

    def gen(v):
        yield env.timeout(1)
        return v

    def bare():
        yield env.timeout(1)

    def waiter(p):
        got = yield p
        return ('got', got)

    values = (None, 0, (1, 2), (), [3], ValueError('x'), StopIteration(5))
    procs = [env.process(gen(v)) for v in values]
    procs.append(env.process(bare()))
    procs.append(env.process(waiter(procs[2])))
    env.run()
    return [(type(p.value).__name__, repr(p.value), p.ok) for p in procs]


def interrupts():
    env = simpy.Environment()
    log = []

    def victim():
        for _ in range(4):
            try:
                yield env.timeout(10)
                log.append(('done', env.now))
            except simpy.Interrupt as e:
                log.append(('int', env.now, e.cause))

    def attacker(p):
        yield env.timeout(1)
        p.interrupt('a')
        yield env.timeout(1)
        p.interrupt('b')
        p.interrupt('c')

    p = env.process(victim())
    p.interrupt('early')
    env.process(attacker(p))
    env.run()
    return log, p.value


def resume_override():
    env = simpy.Environment()
    seen = []

    class P(simpy.Process):
        def _resume(self, event):
            seen.append((env.now, type(event).__name__))
            return super()._resume(event)

    def g():
        yield env.timeout(1)
        yield env.timeout(2)
        return 'r'

    p = P(env, g())
    env.run()
    return seen, p.value


def resume_patched():
    env = simpy.Environment()
    seen = []

    def g():
        yield env.timeout(1)
        yield env.timeout(2)

    p = env.process(g())
    orig = p._resume

    def patched(event):
        seen.append(env.now)
        orig(event)

    p._resume = patched
    env.run()
    return seen, p.ok


class FakeGen:
    """Not a generator, but has send() and throw()."""

    def __init__(self, env):
        self.env = env
        self.n = 0

    def send(self, value):
        self.n += 1
        if self.n > 3:
            raise StopIteration(('fin', value))
        return self.env.timeout(1, self.n)

    def throw(self, exc):
        raise exc


def fake_generator():
    env = simpy.Environment()
    p = env.process(FakeGen(env))
    env.run()
    return p.value, env.now


def bad_yield():
    env = simpy.Environment()

    def g():
        yield 42

    env.process(g())
    try:
        env.run()
    except RuntimeError as e:
        return str(e).splitlines()[-1]


def exc_chain(e):
    out = []
    while e is not None:
        out.append((type(e).__name__, str(e).split(':')[0],
                    e.__suppress_context__, type(e.__cause__).__name__))
        e = e.__context__
    return out


def empty_step():
    env = simpy.Environment()
    try:
        env.step()
    except simpy.core.EmptySchedule as e:
        return exc_chain(e)


def run_until_untriggered():
    env = simpy.Environment()
    env.timeout(1)
    try:
        env.run(until=env.event())
    except RuntimeError as e:
        return exc_chain(e)


def failed_event_crash():
    env = simpy.Environment()
    env.event().fail(KeyError('k'))
    try:
        env.run()
    except KeyError as e:
        return exc_chain(e)


def step_patched_midrun():
    env = simpy.Environment()
    calls = []
    log = []
    mixed_model(env, log)
    orig = env.step

    def tracing():
        calls.append(env.now)
        orig()

    def installer():
        yield env.timeout(5)
        env.step = tracing
        yield env.timeout(5)
        del env.step

    env.process(installer())
    env.run(until=15)
    return calls, log


def copy_env():
    import copy
    import pickle
    out = []
    for setup in ('fresh', 'events'):
        env = simpy.Environment(1)
        if setup == 'events':
            env.timeout(2, 'x')
            env.event().succeed(3)
        for name, fn in (('copy', copy.copy), ('deepcopy', copy.deepcopy),
                         ('pickle', lambda e: pickle.loads(pickle.dumps(e)))):
            try:
                e2 = fn(env)
                q = [(t, p, i, type(ev).__name__) for t, p, i, ev in e2._queue]
                out.append((setup, name, type(e2).__name__, e2.now, q,
                            e2._queue is env._queue, e2.process.__self__ is env))
            except Exception as ex:  # noqa: BLE001
                out.append((setup, name, type(ex).__name__))
    return out


def copy_objects():
    import copy
    env = simpy.Environment()
    res = simpy.Resource(env, 1)
    store = simpy.Store(env, 2)
    cont = simpy.Container(env, 10, init=3)

    def g():
        yield env.timeout(1)

    objs = {
        'event': env.event(),
        'event ok': env.event().succeed('v'),
        'timeout': env.timeout(2, 'w'),
        'process': env.process(g()),
        'request': res.request(),
        'resource': res,
        'store put': store.put('i'),
        'store': store,
        'container get': cont.get(1),
        'container': cont,
        'condition': env.timeout(1) & env.timeout(2),
    }
    out = []
    for name, obj in objs.items():
        for how, fn in (('copy', copy.copy), ('deepcopy', copy.deepcopy)):
            try:
                c = fn(obj)
            except Exception as ex:  # noqa: BLE001
                out.append((name, how, type(ex).__name__))
                continue
            row = [name, how, type(c).__name__, c is obj]
            for attr in ('_value', '_ok', 'callbacks', 'env', '_delay',
                         'resource', 'item', 'amount', 'capacity', 'items',
                         'users', 'queue', 'put_queue', 'get_queue', '_level',
                         '_events', '_count', '_target', '_generator'):
                try:
                    v = getattr(c, attr)
                except AttributeError:
                    continue
                o = getattr(obj, attr)
                desc = repr(v) if isinstance(v, (int, float, str, bool,
                                                 type(None))) else                     type(v).__name__
                if isinstance(v, (list, tuple)):
                    desc += f'[{len(v)}]'
                row.append((attr, desc, v is o))
            out.append(tuple(row))
    return out


def pending_ok():
    env = simpy.Environment()
    ev = env.event()
    try:
        ev.ok
    except AttributeError as e:
        return str(e), getattr(e, 'name', None), getattr(e, 'obj', None) is ev


def subclass_skip_init():
    env = simpy.Environment()

    class MyEvent(simpy.Event):
        def __init__(self, env):
            self.env = env
            self.callbacks = []

    ev = MyEvent(env)
    out = [ev.triggered]
    ev.succeed(5)
    env.run()
    return out + [ev.processed, ev.value, ev.ok]


def event_reinit():
    env = simpy.Environment()
    ev = env.event()
    ev.succeed(1)
    env.run()
    ev.__init__(env)
    return ev.triggered, ev.processed, ev.value, ev.callbacks


def private_ok():
    env = simpy.Environment()
    ev = env.event()
    out = [hasattr(ev, '_ok'), getattr(ev, '_ok', 'absent')]
    try:
        del ev._ok
    except AttributeError as e:
        out.append(str(e))
    ev.succeed()
    out += [hasattr(ev, '_ok'), ev._ok]
    del ev._ok
    out += [hasattr(ev, '_ok'), 'ok' in dir(ev)]
    ev._ok = False
    out.append(ev.ok)
    req = simpy.Resource(env, 1).request()
    out.append(req.usage_since)
    return out


class MockEnv:
    """Duck-typed environment: records what SimPy asks of it."""

    def __init__(self):
        self.calls = []
        self.now = 7
        self.active_process = 'proc?'
        self._active_proc = None

    def schedule(self, event, *args):
        self.calls.append((type(event).__name__, args))


def mock_env():
    env = MockEnv()
    ev = simpy.Event(env)
    ev.succeed('v')
    simpy.Timeout(env, 3, 'w')
    ev2 = simpy.Event(env)
    ev2.fail(ValueError('x'))
    ev3 = simpy.Event(env)
    ev3.trigger(ev)

    def g():
        yield None

    p = simpy.Process(env, g())
    first = (list(env.calls), type(p._target).__name__)
    try:
        p._resume(p._target)
    except RuntimeError as e:
        return first, str(e).splitlines()[-1]
    return first, env.calls[-1]


def callbacks_subclass():
    class Calls(list):
        def __iter__(self):
            log.append('iter')
            return super().__iter__()

    log = []
    env = simpy.Environment()
    ev = env.timeout(1, 'x')
    ev.callbacks = Calls(ev.callbacks)
    ev.callbacks.append(lambda e: log.append(('cb', e.value)))

    def p():
        got = yield ev
        log.append(('proc', got))

    env.process(p())
    env.run()
    return log


class FakeResource:
    def __init__(self, env):
        self._env = env
        self.put_queue = []
        self.get_queue = []
        self.log = []

    def _trigger_get(self, event):
        self.log.append(('tg', type(event).__name__))

    def _trigger_put(self, event):
        self.log.append(('tp', event))
        for e in list(self.put_queue):
            e.succeed('done')
            self.put_queue.remove(e)


def duck_resource():
    env = simpy.Environment()
    res = FakeResource(env)
    put = simpy.resources.base.Put(res)
    env.run()
    return res.log, put.value, put.proc, put.resource is res


def custom_put_for_store():
    from simpy.resources import base

    class MyPut(base.Put):
        def __init__(self, store, item):
            self.item = item
            super().__init__(store)

    env = simpy.Environment()
    st = simpy.Store(env, capacity=1)
    out = []

    def producer():
        for i in range(3):
            yield MyPut(st, i)
            out.append(('put', i, env.now))

    def consumer():
        for _ in range(3):
            yield env.timeout(1)
            out.append(('get', (yield st.get()), env.now))

    env.process(producer())
    env.process(consumer())
    env.run()
    return out


def custom_events_for_container():
    from simpy.resources import base

    class Fill(base.Put):
        def __init__(self, c, amount):
            self.amount = amount
            super().__init__(c)

    class Drain(base.Get):
        def __init__(self, c, amount):
            self.amount = amount
            super().__init__(c)

    env = simpy.Environment()
    c = simpy.Container(env, 10, init=2)
    f, d = Fill(c, 5), Drain(c, 4)
    env.run()
    return c.level, f.triggered, d.triggered


def dynamic_capacity():
    class Elastic(simpy.Resource):
        @property
        def capacity(self):
            return 1 if self._env.now < 5 else 3

    env = simpy.Environment()
    res = Elastic(env, 1)
    out = []

    def user(i):
        with res.request() as req:
            yield req
            out.append((i, env.now, req.usage_since))
            yield env.timeout(4)

    for i in range(5):
        env.process(user(i))

    def poke():
        yield env.timeout(5)
        res._trigger_put(None)

    env.process(poke())
    env.run()
    return out


def env_properties():
    class Env(simpy.Environment):
        @property
        def now(self):
            return ('now', self._now)

        @property
        def active_process(self):
            return ('ap', self._active_proc is not None)

    env = Env()
    res = simpy.Resource(env, 1)
    pres = simpy.PriorityResource(env, 1)
    out = []

    def p():
        req = res.request()
        yield req
        preq = pres.request(priority=2)
        yield preq
        out.append((req.usage_since, req.proc, preq.time, preq.key))

    env.process(p())
    env.run()
    return out


def triggered_override():
    seen = []

    class LoudPut(simpy.resources.store.StorePut):
        @property
        def triggered(self):
            seen.append(self.item)
            return self._value is not simpy.events.PENDING

    env = simpy.Environment()
    st = simpy.Store(env, capacity=1)
    for i in range(3):
        LoudPut(st, i)
    st.get()
    env.run()
    return seen, st.items


def requeue_priority():
    env = simpy.Environment()
    res = simpy.PriorityResource(env, 1)
    order = []
    reqs = {}

    def user(name, prio):
        req = res.request(priority=prio)
        reqs[name] = req
        with req:
            yield req
            order.append(name)
            yield env.timeout(1)

    def bump():
        yield env.timeout(0.5)
        # Re-prioritize a waiting request, then let another one queue up.
        reqs['c'].key = (-1,) + reqs['c'].key[1:]
        env.process(user('e', 5))

    for name, prio in (('a', 0), ('b', 1), ('c', 3), ('d', 2)):
        env.process(user(name, prio))
    env.process(bump())
    env.run()
    return order


def nan_priority():
    env = simpy.Environment()
    res = simpy.PriorityResource(env, 1)
    order = []

    def user(i, prio):
        with res.request(priority=prio) as req:
            yield req
            order.append(i)
            yield env.timeout(1)

    nan = float('nan')
    for i, prio in enumerate([1, nan, 0, 2, nan, -1, 3, 0.5, nan, 1]):
        env.process(user(i, prio))
    env.run()
    return order


def preemptive_mixin():
    seen = []

    class Audit(simpy.PriorityResource):
        def _do_put(self, event):
            seen.append(('audit', event.priority))
            return super()._do_put(event)

    class Res(simpy.PreemptiveResource, Audit):
        pass

    env = simpy.Environment()
    res = Res(env, 1)

    def user(prio, t):
        yield env.timeout(t)
        with res.request(priority=prio) as req:
            try:
                yield req
                yield env.timeout(3)
            except simpy.Interrupt:
                seen.append(('preempted', prio, env.now))

    env.process(user(2, 0))
    env.process(user(1, 1))
    env.run()
    return seen


def do_put_repatched():
    env = simpy.Environment()
    st = simpy.Store(env, capacity=10)
    seen = []
    orig = st._do_put

    def first(event):
        seen.append(('first', event.item))
        st._do_put = second
        orig(event)
        return True

    def second(event):
        seen.append(('second', event.item))
        orig(event)
        return True

    for i in range(3):
        st.put(i)
    st.items.clear()
    for ev in list(st.put_queue):
        st.put_queue.remove(ev)
    queued = []
    for i in range(3):
        e = simpy.resources.store.StorePut.__new__(
            simpy.resources.store.StorePut)
        simpy.Event.__init__(e, env)
        e.resource = st
        e.item = i
        queued.append(e)
    st.put_queue.extend(queued)
    st._do_put = first
    st._trigger_put(None)
    return seen, st.items


def duck_event_resume():
    class Signal:
        def __init__(self, env, value):
            self.env = env
            self.callbacks = []
            self._ok = True
            self._value = value

        def fire(self):
            self.env.schedule(self)

    env = simpy.Environment()
    out = []
    sig = Signal(env, 'payload')

    def p():
        got = yield sig
        out.append((env.now, got))
        got = yield sig  # Already processed: resumes at once.
        out.append((env.now, got))

    def firer():
        yield env.timeout(2)
        sig.fire()

    env.process(p())
    env.process(firer())
    env.run()
    return out


def schedule_raises_on_exit():
    class Env(simpy.Environment):
        def schedule(self, event, *args):
            if isinstance(event, simpy.Process) and \
                    event._value is not simpy.events.PENDING:
                raise KeyError('no')
            super().schedule(event, *args)

    env = Env()

    def g():
        yield env.timeout(1)
        return 'r'

    env.process(g())
    try:
        env.run()
    except KeyError as e:
        return exc_chain(e)


def generic_alias():
    import typing
    from simpy.resources import base
    a = base.Put[simpy.Resource]
    out = [repr(a), typing.get_origin(a) is base.Put, typing.get_args(a),
           repr(base.BaseResource[base.Put, base.Get])]
    try:
        isinstance(None, a)
    except TypeError as e:
        out.append(type(e).__name__)
    return out


def users_items_subclass():
    class L(list):
        pass

    env = simpy.Environment()
    res = simpy.Resource(env, 1)
    res.users = L()
    st = simpy.Store(env)
    st.items = L()
    out = []

    def p():
        with res.request() as req:
            yield req
            out.append(type(res.users).__name__)
        yield st.put(1)
        out.append((type(st.items).__name__, (yield st.get())))

    env.process(p())
    env.run()
    return out


def events_tuple_subclass():
    class T(tuple):
        pass

    env = simpy.Environment()
    c = env.timeout(1) & env.timeout(2)
    try:
        c._events = T(c._events)
        out = [type(c._events).__name__]
    except TypeError as e:
        out = [type(e).__name__]
    env.run()
    return out + [c.ok]


probe('mock env', mock_env)
probe('callbacks subclass', callbacks_subclass)
probe('duck resource', duck_resource)
probe('custom put for store', custom_put_for_store)
probe('custom events for container', custom_events_for_container)
probe('dynamic capacity', dynamic_capacity)
probe('env properties', env_properties)
probe('triggered override', triggered_override)
probe('requeue priority', requeue_priority)
probe('nan priority', nan_priority)
probe('preemptive mixin', preemptive_mixin)
probe('do_put repatched', do_put_repatched)
probe('duck event resume', duck_event_resume)
probe('schedule raises on exit', schedule_raises_on_exit)
probe('generic alias', generic_alias)
probe('users/items subclass', users_items_subclass)
probe('events tuple subclass', events_tuple_subclass)


def sorted_queue_edges():
    from simpy.resources.resource import SortedQueue
    out = []

    def run(prios, cls=None):
        env = simpy.Environment()
        res = simpy.PriorityResource(env, 1)
        order = []

        def user(i, prio):
            req = (cls(res, prio) if cls else res.request(priority=prio))
            with req:
                yield req
                order.append(i)
                yield env.timeout(1)

        for i, prio in enumerate(prios):
            env.process(user(i, prio))
        env.run()
        return order

    big = 2 ** 60
    out.append(run([big + 1, big, big + 1, 3, big - 1, 2 ** 53 + 1, 2 ** 53]))
    out.append(run([1, 1.0, True, 0, False, -0.0, 0.0, 2.5, 1, -1]))
    out.append(run([3, 1, 2, float('inf'), -float('inf'), 1e300, -5]))
    out.append(run([(1, 2), (1, 1), (0, 5), (1, 1)]))
    out.append(run(['b', 'a', 'c', 'a']))

    class MyReq(simpy.resources.resource.PriorityRequest):
        pass

    out.append(run([2, 1, 3, 1, 0], MyReq))

    # Foreign items in the queue, then a regular request.
    class Item:
        def __init__(self, key):
            self.key = key

    q = SortedQueue()
    env = simpy.Environment()
    res = simpy.PriorityResource(env, 1)
    reqs = [res.request(priority=p) for p in (5, 1, 3)]
    for it in (Item((2, 0, False)), reqs[1], Item((4, 0, True)), reqs[2]):
        q.append(it)
    q.append(reqs[0])
    out.append([getattr(e, 'key', None) for e in q])

    # Unsorted queue (mutated by hand) gets fully re-sorted.
    q2 = SortedQueue()
    for r in reqs:
        q2.append(r)
    list.reverse(q2)
    q2.append(res.request(priority=2))
    out.append([e.key[0] for e in q2])

    # maxlen
    q3 = SortedQueue(maxlen=2)
    q3.append(reqs[0])
    q3.append(reqs[1])
    try:
        q3.append(reqs[2])
    except RuntimeError as e:
        out.append(str(e))
    out.append([e.key[0] for e in q3])

    # A mixin between SortedQueue and list that changes sort().
    log = []

    class Rev(list):
        def sort(self, *, key=None, reverse=False):
            log.append(len(self))
            super().sort(key=key, reverse=True)

    class Q(SortedQueue, Rev):
        pass

    q4 = Q()
    for r in reqs:
        q4.append(r)
    out.append(([e.key[0] for e in q4], log))
    return out


probe('sorted queue edges', sorted_queue_edges)


def module_namespaces():
    import importlib
    import pickle
    out = []
    for m in ('simpy.core', 'simpy.events', 'simpy.resources.base',
              'simpy.resources.container', 'simpy.resources.resource',
              'simpy.resources.store'):
        mod = importlib.import_module(m)
        out.append((m, sorted(k for k in vars(mod) if not k.startswith('_'))))
    import simpy.core as c
    import simpy.events as e
    import simpy.resources.base as b
    for o in (c.T, e.EventPriority, b.ResourceType, b.PutType, b.GetType):
        try:
            same = pickle.loads(pickle.dumps(o)) is o
        except Exception as ex:
            same = type(ex).__name__
        out.append((repr(o), o.__module__, same))
    return out


probe('module namespaces', module_namespaces)


probe('private ok', private_ok)
probe('pending ok', pending_ok)
probe('subclass skip init', subclass_skip_init)
probe('event reinit', event_reinit)
probe('copy objects', copy_objects)
probe('process returns', process_returns)
probe('interrupts', interrupts)
probe('resume override', resume_override)
probe('resume patched', resume_patched)
probe('fake generator', fake_generator)
probe('bad yield', bad_yield)
probe('empty step', empty_step)
probe('run until untriggered', run_until_untriggered)
probe('failed event crash', failed_event_crash)
probe('step patched midrun', step_patched_midrun)
probe('copy env', copy_env)
probe('schedule override', schedule_override)
probe('schedule patched', schedule_patched)
probe('queue access at 0', lambda: queue_access_midrun(0.5))
probe('queue access at 7', lambda: queue_access_midrun(7))
probe('monitored step', monitored_step)
probe('eid access', eid_access)
probe('time int/float', lambda: time_keys(0, [3, 1.5, 3.0, 2, 0, 1e-9]))
probe('time -0.0', lambda: time_keys(-0.0, [0, 0.0, 1]))
probe('time big int', lambda: time_keys(2 ** 60, [1, 2, 2 ** 54]))
probe('time float initial', lambda: time_keys(0.5, [1, 2]))
probe('time nan', lambda: time_keys(0, [float('nan'), 1, 2]))
probe('time inf', lambda: time_keys(0, [float('inf'), 1]))
probe('time Fraction mix', lambda: time_keys(0, [Fraction(1, 3), 1, 0.25]))
probe('priority schedule ints', lambda: schedule_priorities([3, -1, 0, 1, 1, 2]))
probe('priority schedule float', lambda: schedule_priorities([1, 0.5, 0, 1.5]))
probe('priority schedule big', lambda: schedule_priorities([2 ** 70, 1, -2 ** 70]))
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
