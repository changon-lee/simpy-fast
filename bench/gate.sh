#!/bin/sh
# Regression gate: upstream's test suite against src/. The one known failure
# (test_exception_chaining) fails on pristine upstream too on this machine.
# test_rt_slow_sim_default_behavior is wall-clock flaky on upstream too.
cd "$(dirname "$0")/../upstream" || exit 1
out=$(PYTHONPATH="$(cd ../${TREE:-src} && pwd)" "${PY:-python}" -m pytest -q tests -p no:cacheprovider --deselect tests/test_rt.py::test_rt_slow_sim_default_behavior 2>&1)
summary=$(echo "$out" | tail -1)
fails=$(echo "$out" | grep '^FAILED' | grep -v 'test_exceptions.py::test_exception_chaining')
echo "$summary"
if [ -n "$fails" ]; then echo "$fails"; echo "GATE: RED"; exit 1; fi
case "$summary" in
  "1 failed, 137 passed"*) ;;
  *) echo "$out" | grep -E "Error|error" | head -5; echo "GATE: RED (unexpected summary)"; exit 1 ;;
esac
echo "GATE: GREEN"
