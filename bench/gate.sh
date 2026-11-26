#!/bin/bash
# Regression gate for the SimPy tree in $TREE (default src) under $PY.
#  1. upstream's test suite. test_exception_chaining fails on pristine
#     upstream on Windows too (path regex), so exactly that one may fail.
#  2. test_rt.py measures wall-clock time and is flaky on upstream as well;
#     it must pass in one of three attempts. test_rt_slow_sim_default_behavior
#     fails on upstream every time under Python 3.12, so it is left out.
#  3. bench/compat.py boundary probes must print exactly what upstream prints.
#  4. bench/leakcheck.py: no leaked references.
here="$(cd "$(dirname "$0")/.." && pwd)"
tree="$here/${TREE:-src}"
py="${PY:-python}"
cd "$here/upstream" || exit 1

out=$(PYTHONPATH="$tree" "$py" -m pytest -q tests -p no:cacheprovider --ignore=tests/test_rt.py 2>&1)
summary=$(echo "$out" | tail -1)
echo "$summary"
fails=$(echo "$out" | grep '^FAILED' | grep -v 'test_exceptions.py::test_exception_chaining')
if [ -n "$fails" ]; then echo "$fails"; echo "GATE: RED"; exit 1; fi
case "$summary" in
  "1 failed, 129 passed"*) ;;
  *) echo "$out" | grep -E "Error|error" | head -5; echo "GATE: RED (unexpected summary)"; exit 1 ;;
esac

rt_ok=
for attempt in 1 2 3; do
  rt=$(PYTHONPATH="$tree" "$py" -m pytest -q tests/test_rt.py -p no:cacheprovider \
       --deselect tests/test_rt.py::test_rt_slow_sim_default_behavior 2>&1 | tail -1)
  case "$rt" in
    "8 passed"*) rt_ok=1; break ;;
  esac
done
echo "rt: $rt (attempt $attempt)"
if [ -z "$rt_ok" ]; then echo "GATE: RED (test_rt.py failed 3 times)"; exit 1; fi

base_out=$("$py" "$here/bench/compat.py" "$here/upstream/src" 2>/dev/null)
tree_out=$("$py" "$here/bench/compat.py" "$tree" 2>/dev/null)
if [ "$base_out" != "$tree_out" ]; then
  diff <(echo "$base_out") <(echo "$tree_out")
  echo "GATE: RED (compat probes differ from upstream)"; exit 1
fi
echo "compat: identical to upstream ($(echo "$tree_out" | wc -l) probes)"

if ! "$py" "$here/bench/leakcheck.py" "$tree" >/dev/null 2>&1; then
  "$py" "$here/bench/leakcheck.py" "$tree"
  echo "GATE: RED (reference leak)"; exit 1
fi
echo "leakcheck: no leaked references"
echo "GATE: GREEN"
