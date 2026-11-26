"""A/B benchmark driver: alternates SimPy trees in fresh subprocesses.

Usage: python bench/bench.py [--rounds N] [--only w1,w2] TREE [TREE ...]
TREE is a label=path pair, e.g. base=upstream/src opt=src.

Prints, per workload, the median seconds, range and speedup vs the first tree,
and fails if any tree's trace digest differs from the first tree's.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument('--rounds', type=int, default=5)
ap.add_argument('--only', default='')
ap.add_argument('--json', default='')
ap.add_argument('trees', nargs='+')
args = ap.parse_args()

trees = [t.split('=', 1) for t in args.trees]
only = [w for w in args.only.split(',') if w]
runs = {label: [] for label, _ in trees}
for r in range(args.rounds):
    order = trees if r % 2 == 0 else trees[::-1]
    for label, path in order:
        cmd = [sys.executable, os.path.join(HERE, 'run_one.py'), path, *only]
        res = json.loads(subprocess.run(
            cmd, check=True, capture_output=True, text=True).stdout)
        runs[label].append(res)
    print(f'round {r + 1}/{args.rounds} done', file=sys.stderr)

base_label = trees[0][0]
workloads = list(runs[base_label][0]['results'])
errors = 0
summary = {}
hdr = f"{'workload':<20}" + ''.join(
    f'{label + " med(s)":>14}{"range":>15}' for label, _ in trees) + ''.join(
    f'{"x vs " + base_label:>12}' for label, _ in trees[1:])
print(hdr)
totals = {label: 0.0 for label, _ in trees}
for w in workloads:
    ref_digest = runs[base_label][0]['results'][w]['digest']
    row = f'{w:<20}'
    meds = {}
    for label, _ in trees:
        secs = [run['results'][w]['sec'] for run in runs[label]]
        for run in runs[label]:
            if run['results'][w]['digest'] != ref_digest:
                errors += 1
                print(f'!! digest mismatch: {label} {w}', file=sys.stderr)
        meds[label] = statistics.median(secs)
        totals[label] += meds[label]
        row += f'{meds[label]:>14.3f}{f"{min(secs):.3f}-{max(secs):.3f}":>15}'
    for label, _ in trees[1:]:
        row += f'{meds[base_label] / meds[label]:>12.2f}'
    summary[w] = meds
    print(row)
row = f"{'TOTAL':<20}" + ''.join(
    f'{totals[label]:>14.3f}{"":>15}' for label, _ in trees) + ''.join(
    f'{totals[base_label] / totals[label]:>12.2f}' for label, _ in trees[1:])
print(row)
print(f'rounds={args.rounds} digest_mismatches={errors} '
      + ' '.join(f'{l}:{runs[l][0]["simpy_file"]}' for l, _ in trees))
if args.json:
    with open(args.json, 'w') as f:
        json.dump({'summary': summary, 'totals': totals, 'runs': runs}, f)
sys.exit(1 if errors else 0)
