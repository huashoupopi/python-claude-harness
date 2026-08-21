#!/bin/bash
cd "$1"
"${BENCH_PYTHON:-python}" -m pytest -q