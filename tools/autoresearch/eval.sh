#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0
#
# One autoresearch evaluation of the Horizon checkout this script lives in.
# Gates: tests, lint, byte-identical output against the golden snapshot.
# Metrics: LOC of horizon/, CLI import time, generation wall time of the
# synthetic study and of the full production IMO study (3 000 realizations).
# Prints one TSV line: status loc import_s gen_s imo_s  (status = ok | FAIL:<gate>)
#
#   tools/autoresearch/eval.sh            evaluate this checkout
#   tools/autoresearch/eval.sh --golden   (re)write the golden snapshot from this checkout
#
# Needs: fixtures in $AR_FIXTURES (default /home/user/ar/fixtures2, see program.md).
set -uo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
FIX=${AR_FIXTURES:-/home/user/ar/fixtures2}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ar_eval.XXXX")
LOG=$WORK/eval.log
trap 'rm -rf "$WORK"' EXIT
export PYTHONPATH=$REPO PATH=$FIX/stubbin:$PATH HORIZON_TASK_ENV=PUEUE_STUB_LOG
fail() { echo -e "FAIL:$1\t-\t-\t-\t-"; tail -40 "$LOG" >&2; exit 1; }

cd "$REPO"
if [[ ${1:-} != --golden ]]; then
  python -m pytest -q -x -p no:cacheprovider >"$LOG" 2>&1 || fail tests
  { flake8 horizon tests && isort horizon tests --check-only; } >>"$LOG" 2>&1 || fail lint
fi

# Generate each fixture study through the real CLI, normalise absolute paths.
for study in synth:study.hor imo:october/run.hor; do
  name=${study%%:*}; hor=${study#*:}
  cp -r "$FIX/$name" "$WORK/$name"
  export PUEUE_STUB_LOG=$WORK/$name.queue
  ( cd "$WORK/$name/$(dirname "$hor")" &&
    python -m horizon "$(basename "$hor")" --pueue-cli --output-dir "$WORK/${name}_out" ) >>"$LOG" 2>&1 || fail "run_$name"
  sed -i "s#$WORK#WORK#g" "$PUEUE_STUB_LOG"
  find "$WORK/$name" -name output.log -delete
  python tools/benchmark/manifest.py snapshot "$WORK/${name}_out" "$WORK/$name" -o "$WORK/$name.json" >>"$LOG" 2>&1
done

if [[ ${1:-} == --golden ]]; then
  mkdir -p "$FIX/golden"; cp "$WORK"/*.json "$WORK"/*.queue "$FIX/golden/"; echo "golden written"; exit 0
fi
for name in synth imo; do
  python tools/benchmark/manifest.py compare "$FIX/golden/$name.json" "$WORK/$name.json" >>"$LOG" 2>&1 || fail "golden_$name"
  python tools/benchmark/manifest.py compare "$FIX/golden/$name.queue" "$WORK/$name.queue" --unordered >>"$LOG" 2>&1 || fail "queue_$name"
done

loc=$(cat horizon/*.py horizon/*/*.py | grep -cvE '^\s*(#|$)')
best() { python - "$@" <<'PY'
import subprocess, sys, time
ts = []
for _ in range(3):
    t = time.perf_counter(); subprocess.run(sys.argv[1:], check=True, capture_output=True); ts.append(time.perf_counter() - t)
print(f"{min(ts):.2f}")
PY
}
imp=$(best python -c "import horizon.__main__") || fail import_time
# Timed runs write to tmpfs when available: disk noise otherwise swamps 10 % effects.
OUT=$(mktemp -d "$( [[ -d /dev/shm ]] && echo /dev/shm || echo "$WORK" )/ar_out.XXXX"); trap 'rm -rf "$WORK" "$OUT"' EXIT
export PUEUE_STUB_LOG=/dev/null
cd "$WORK/synth"
gen=$(best python -m horizon study.hor --pueue-cli --output-dir "$OUT/synth") || fail gen_time
cp -r "$FIX/imo_full" "$WORK/imo_full" && cd "$WORK/imo_full/october"
imo=$(best python -m horizon run.hor --pueue-cli --output-dir "$OUT/imo") || fail imo_time
echo -e "ok\t$loc\t$imp\t$gen\t$imo"
