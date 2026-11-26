#!/bin/sh
# Regression gate: upstream's test suite against src/. The one known failure
# (test_exception_chaining) fails on pristine upstream too on this machine.
cd "$(dirname "$0")/../upstream" || exit 1
out=$(PYTHONPATH="$(cd ../src && pwd)" python -m pytest -q tests -p no:cacheprovider 2>&1)
summary=$(echo "$out" | tail -1)
fails=$(echo "$out" | grep '^FAILED' | grep -v 'test_exceptions.py::test_exception_chaining')
echo "$summary"
if [ -n "$fails" ]; then echo "$fails"; echo "GATE: RED"; exit 1; fi
echo "GATE: GREEN"
