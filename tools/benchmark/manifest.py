# SPDX-FileCopyrightText: 2026 Fonden Mærsk Mc-Kinney Møller Center for Zero Carbon Shipping
# SPDX-License-Identifier: Apache-2.0

"""Golden-output manifests for verifying that a performance change did not
alter what Horizon produces.

``snapshot`` walks one or more directories and records the SHA-256 of every
file (keyed by path relative to each root). ``compare`` diffs two manifests
and exits non-zero on any added, removed, or changed file — so equivalence
checks can gate a refactor in CI or locally:

    # before the change
    python tools/benchmark/manifest.py snapshot out/ output/ -o golden.json
    # after the change (fresh run into the same relative layout)
    python tools/benchmark/manifest.py snapshot out/ output/ -o candidate.json
    python tools/benchmark/manifest.py compare golden.json candidate.json

Queue submissions are compared with ``compare --unordered`` on the stub
pueue's log files: generation runs in a thread pool, so submission order is
not deterministic and only the *set* of submitted commands must match.
"""

import argparse
import hashlib
import json
import os
import sys


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(roots, output):
    manifest = {}
    for root in roots:
        root = os.path.abspath(root)
        base = os.path.basename(root.rstrip(os.sep))
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.join(base, os.path.relpath(full, root))
                manifest[rel.replace(os.sep, "/")] = _hash_file(full)
    with open(output, "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    print(f"{len(manifest)} files -> {output}")


def compare(golden_path, candidate_path, unordered_as_lines=False):
    if unordered_as_lines:
        # Compare two plain-text logs as unordered line sets (queue submissions).
        with open(golden_path) as fh:
            golden = sorted(line.rstrip("\n") for line in fh if line.strip())
        with open(candidate_path) as fh:
            candidate = sorted(line.rstrip("\n") for line in fh if line.strip())
        if golden == candidate:
            print(f"OK: {len(golden)} lines match (order-insensitive)")
            return 0
        missing = set(golden) - set(candidate)
        added = set(candidate) - set(golden)
        for line in sorted(missing)[:10]:
            print(f"MISSING: {line}")
        for line in sorted(added)[:10]:
            print(f"ADDED:   {line}")
        print(f"FAIL: {len(missing)} missing, {len(added)} added "
              f"(golden {len(golden)} vs candidate {len(candidate)} lines)")
        return 1

    with open(golden_path) as fh:
        golden = json.load(fh)
    with open(candidate_path) as fh:
        candidate = json.load(fh)

    missing = sorted(set(golden) - set(candidate))
    added = sorted(set(candidate) - set(golden))
    changed = sorted(k for k in set(golden) & set(candidate) if golden[k] != candidate[k])

    for k in missing[:20]:
        print(f"MISSING: {k}")
    for k in added[:20]:
        print(f"ADDED:   {k}")
    for k in changed[:20]:
        print(f"CHANGED: {k}")

    if missing or added or changed:
        print(f"FAIL: {len(missing)} missing, {len(added)} added, {len(changed)} changed "
              f"of {len(golden)} golden files")
        return 1
    print(f"OK: {len(golden)} files identical")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_snap = sub.add_parser("snapshot", help="hash every file under the given directories")
    ap_snap.add_argument("roots", nargs="+")
    ap_snap.add_argument("-o", "--output", required=True)

    ap_cmp = sub.add_parser("compare", help="diff two manifests (or two logs with --unordered)")
    ap_cmp.add_argument("golden")
    ap_cmp.add_argument("candidate")
    ap_cmp.add_argument("--unordered", action="store_true",
                        help="treat inputs as plain-text logs compared as unordered line sets")

    args = ap.parse_args()
    if args.cmd == "snapshot":
        snapshot(args.roots, args.output)
        return 0
    return compare(args.golden, args.candidate, unordered_as_lines=args.unordered)


if __name__ == "__main__":
    sys.exit(main())
