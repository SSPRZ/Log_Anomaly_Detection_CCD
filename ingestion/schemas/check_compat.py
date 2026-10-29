"""CI contract test: Avro schemas must stay BACKWARD compatible.

    python ingestion/schemas/check_compat.py                     # local structural check vs git HEAD~1
    python ingestion/schemas/check_compat.py --registry URL      # ask the Schema Registry (Apicurio/Confluent API)

Local rule set (subset of Avro BACKWARD compatibility, enough to catch the
usual mistakes): a new schema may add fields only with a default, may not
remove fields that have no default, and may not change a field's type except
by widening it into a union that contains the old type.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def _types(t):
    return {json.dumps(x, sort_keys=True) for x in (t if isinstance(t, list) else [t])}


def backward_compatible(old: dict, new: dict) -> list[str]:
    errors = []
    old_f = {f["name"]: f for f in old.get("fields", [])}
    new_f = {f["name"]: f for f in new.get("fields", [])}
    for name, f in new_f.items():
        if name not in old_f and "default" not in f:
            errors.append(f"field '{name}' added without a default")
    for name, f in old_f.items():
        if name not in new_f:
            continue  # readers using the new schema simply ignore it
        if not _types(f["type"]) <= _types(new_f[name]["type"]) and f["type"] != new_f[name]["type"]:
            errors.append(f"field '{name}' changed type {f['type']} -> {new_f[name]['type']}")
    return errors


def previous_version(path: Path) -> dict | None:
    rel = path.relative_to(HERE.parents[1]).as_posix()
    try:
        out = subprocess.run(["git", "show", f"HEAD~1:{rel}"], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=None, help="e.g. http://schema-registry:8080/apis/ccompat/v7")
    a = ap.parse_args(argv)
    failed = False
    for path in sorted(HERE.glob("*.avsc")):
        new = json.loads(path.read_text(encoding="utf-8"))
        if a.registry:
            subject = f"{new['namespace']}.{new['name']}-value"
            r = requests.post(f"{a.registry}/compatibility/subjects/{subject}/versions/latest",
                              json={"schema": json.dumps(new)}, timeout=10)
            ok = r.status_code == 404 or r.json().get("is_compatible", False)
            print(f"{path.name}: {'compatible' if ok else 'INCOMPATIBLE'} (registry)")
            failed |= not ok
            continue
        old = previous_version(path)
        errs = backward_compatible(old, new) if old else []
        print(f"{path.name}: {'ok' if not errs else 'INCOMPATIBLE: ' + '; '.join(errs)}")
        failed |= bool(errs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
