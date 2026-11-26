"""Query a winprof.py JSON profile.

Usage:
  python bench/winprof_query.py PROFILE.json incl [N]        inclusive top N
  python bench/winprof_query.py PROFILE.json callers FUNC     who calls FUNC
  python bench/winprof_query.py PROFILE.json under FUNC [N]   self time under FUNC
  python bench/winprof_query.py PROFILE.json groups           cost groups
FUNC is a substring of the symbol (module!function).
"""
import collections
import json
import sys

stacks = json.load(open(sys.argv[1]))['stacks']
n = len(stacks)
cmd = sys.argv[2]


def pct(v):
    return f'{100 * v / n:6.1f}%'


if cmd == 'incl':
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    c = collections.Counter()
    for s in stacks:
        for f in set(s):
            c[f] += 1
    for k, v in c.most_common(top):
        print(pct(v), k)
elif cmd == 'callers':
    func = sys.argv[3]
    c = collections.Counter()
    for s in stacks:
        for i, f in enumerate(s):
            if func in f:
                c[s[i + 1] if i + 1 < len(s) else '<root>'] += 1
                break
    for k, v in c.most_common(25):
        print(pct(v), k)
elif cmd == 'under':
    func = sys.argv[3]
    top = int(sys.argv[4]) if len(sys.argv) > 4 else 30
    c = collections.Counter()
    tot = 0
    for s in stacks:
        if any(func in f for f in s):
            tot += 1
            c[s[0]] += 1
    print(pct(tot), 'total under', func)
    for k, v in c.most_common(top):
        print(pct(v), k)
elif cmd == 'groups':
    # Leaf (self) time charged to cost groups, first match wins.
    groups = [
        ('event-queue heap (heapq + tuple compare)',
         lambda s: any(f.endswith(('!siftup', '!siftdown', '!heappop_internal',
                                   '!heappush', '!_heapq_heappush',
                                   '!_heapq_heappop')) for f in s[:6])),
        ('gc collection', lambda s: any('gc_collect' in f or f.endswith(
            ('!update_refs', '!subtract_refs', '!move_unreachable',
             '!deduce_unreachable', '!gc_collect_main', '!_Py_RunGC'))
            for f in s)),
        ('python bytecode (model code)',
         lambda s: s[0].endswith('!_PyEval_EvalFrameDefault')),
    ]
    c = collections.Counter()
    for s in stacks:
        for name, pred in groups:
            if pred(s):
                c[name] += 1
                break
        else:
            c['rest'] += 1
    for k, v in c.most_common():
        print(pct(v), k)
