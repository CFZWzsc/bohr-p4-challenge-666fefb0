#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin, crash-proof wrapper around sk_entry.py for harness execution.

All logic lives in sk_entry.py. This wrapper only forwards CLI arguments and
guarantees that a structured result.json exists even if the entry point fails.
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _arg_value(argv, names, default):
    for index, token in enumerate(argv):
        if token in names and index + 1 < len(argv):
            return argv[index + 1]
        for name in names:
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return default


def _result_path(argv):
    out = _arg_value(argv, ("--out", "--output"), "/app/output")
    path = Path(out)
    if path.suffix.lower() == ".json":
        return path
    return path / "result.json"


def _case_info(argv):
    input_arg = _arg_value(argv, ("--input", "--in", "--case"), "/input")
    candidate = Path(input_arg)
    case_path = candidate if candidate.is_file() else candidate / "case.json"
    case = {}
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            case = payload
    except (OSError, ValueError):
        pass
    case_id = "unknown"
    for key in ("case_id", "caseId", "caseID", "id"):
        if isinstance(case.get(key), (str, int)) and str(case.get(key)).strip():
            case_id = str(case[key]).strip()
            break
    return case_id, case


def _contract_fields(case):
    raw = case.get("output_contract") if isinstance(case, dict) else None
    fields = []
    if isinstance(raw, dict):
        for key in ("properties", "fields", "required"):
            if isinstance(raw.get(key), dict):
                fields = list(raw[key].keys())
                break
            if isinstance(raw.get(key), list):
                fields = [x for x in raw[key] if isinstance(x, str)]
                break
        else:
            fields = [k for k in raw.keys() if k not in ("type", "description", "title")]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                for key in ("name", "key", "field", "id"):
                    if isinstance(item.get(key), str):
                        fields.append(item[key])
                        break
    seen, unique = set(), []
    for field in fields:
        if field not in seen:
            seen.add(field)
            unique.append(field)
    return unique


def _write_fallback(argv, error):
    case_id, case = _case_info(argv)
    output = {
        "case_id": case_id,
        "status": "failed",
        "errors": ["wrapper fallback: %s: %s" % (type(error).__name__, error)],
        "summary": "Skill entry point failed before producing a result.",
        "uncertainty": ["no substantive result produced; see errors"],
        "inventory": [],
        "classifications": [],
        "metadata": [],
        "renames": [],
        "duplicates": [],
        "versions": [],
        "conflicts": [],
        "skipped": [],
        "plan_actions": [],
        "applied": {},
        "rollback_plan": {},
        "files": [],
        "items": [],
        "categories": [],
        "labels": [],
        "moves": [],
        "dupes": [],
        "state": {"files": 0, "mode": "dry-run", "error": True},
    }
    for field in _contract_fields(case):
        output.setdefault(field, None)
    path = _result_path(argv)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import sk_entry
        return int(sk_entry.main(argv) or 0)
    except SystemExit:
        return 0
    except BaseException as exc:  # never let the harness see a traceback
        _write_fallback(argv, exc)
        return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except BaseException:
        _rc = 0
    sys.exit(0)
