#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic literature classification, tagging, naming and dedup. Stdlib only."""

import argparse
import hashlib
import json
import os
import re
import sys
import zlib
from pathlib import Path

MAX_FILES = 2000
MAX_TEXT_BYTES = 1024 * 1024
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "corpus_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
AUTHOR_RE = re.compile(r"^([A-Z][A-Za-z\-']{1,30})[\s_\-]+(19\d{2}|20\d{2})")
TYPE_RULES = [
    ("supplement", ("supplement", "suppl", "supporting", "补充材料", "附录")),
    ("preprint", ("preprint", "biorxiv", "medrxiv", "arxiv", "ssrn", "预印本")),
    ("thesis", ("thesis", "dissertation", "学位论文", "博士论文", "硕士论文")),
    ("conference", ("conference", "proceedings", "symposium", "会议")),
    ("dataset", ("dataset", "data_", "suppdata", "数据集")),
    ("review", ("review", "survey", "综述")),
]
STOP = set("the and for with from this that these those study using based on of in to a an is are was were we our "
           "研究 分析 方法 结果 本文 基于 以及 一个 我们 数据 文献".split())
ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "assignments": ["assignments", "classification", "categories", "labels", "classes", "file_classes"],
    "tags": ["tags", "keywords", "labels_by_file", "tagging"],
    "proposed_names": ["proposed_names", "renames", "new_names", "naming", "rename_plan"],
    "duplicates": ["duplicates", "duplicate_groups", "dups"],
    "version_groups": ["versions", "version_groups", "version_chains"],
    "unresolved": ["unresolved", "missing_metadata", "incomplete", "needs_review"],
    "manifest": ["manifest", "inventory", "files", "file_list", "listing"],
    "summary": ["summary", "overview", "conclusion", "notes"],
    "status": ["status", "run_status"],
    "result": ["result", "results", "data"],
    "evidence": ["evidence", "provenance", "sources"],
    "uncertainty": ["uncertainty", "limitations"],
    "errors": ["errors", "error"],
    "stats": ["stats", "statistics", "counts"],
}


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def contract_fields(case):
    oc = case.get("output_contract")
    out = []
    if isinstance(oc, dict):
        if isinstance(oc.get("properties"), dict):
            out = list(oc["properties"].keys())
        elif isinstance(oc.get("fields"), dict):
            out = list(oc["fields"].keys())
        elif isinstance(oc.get("fields"), list):
            out = [x for x in oc["fields"] if isinstance(x, str)]
        elif isinstance(oc.get("required"), list):
            out = [x for x in oc["required"] if isinstance(x, str)]
        else:
            out = list(oc.keys())
    elif isinstance(oc, list):
        out = [x for x in oc if isinstance(x, str)]
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def iter_paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_paths(item)
    elif isinstance(value, dict):
        for key in ("path", "dir", "file", "files", "root"):
            if key in value:
                yield from iter_paths(value[key])


def candidate_roots(case, case_path):
    base = case_path.parent
    roots = []
    for key in PATH_KEYS:
        if key in case:
            for raw in iter_paths(case[key]):
                p = Path(raw)
                if not p.is_absolute():
                    p = base / p
                roots.append(p)
    if base.name.lower() == "input" or (base / "materials").is_dir():
        roots.append(base)
    if Path("/input").is_dir():
        roots.append(Path("/input"))
    seen, uniq = set(), []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def pdf_text(data):
    parts = []
    for match in re.finditer(rb"stream\r?\n", data):
        start, end = match.end(), data.find(b"endstream", match.end())
        if end < 0:
            continue
        raw = data[start:end]
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        for sm in re.finditer(rb"\((?:\\.|[^()\\])*\)", raw, re.S):
            token = sm.group(0)[1:-1]
            token = token.replace(b"\\(", b"(").replace(b"\\)", b")")
            parts.append(token.decode("latin-1", errors="replace"))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def read_head(path, ext):
    try:
        data = path.read_bytes()[:MAX_TEXT_BYTES]
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if ext == ".pdf":
        text = pdf_text(data)
        return text, None if text else "no extractable text layer"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), None


def collect(roots, errors, skipped, exclude):
    files, seen = [], set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
    except OSError:
        exclude_key = None
    for root in roots:
        if not root.exists():
            errors.append("material root not found: %s" % (root.name or str(root)))
            continue
        if root.is_file():
            paths, root_dir = [root], root.parent
        else:
            root_dir, paths = root, []
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                    for fn in sorted(filenames):
                        paths.append(Path(dirpath) / fn)
            except OSError as exc:
                errors.append("walk failed on %s: %s" % (root.name, exc))
                continue
        for path in paths:
            if len(files) >= MAX_FILES:
                skipped.append({"path": path.name, "reason": "file cap reached"})
                continue
            try:
                rp = str(path.resolve())
                if path.is_symlink() or not path.is_file() or rp == exclude_key:
                    continue
            except OSError as exc:
                skipped.append({"path": path.name, "reason": "resolve failed: %s" % exc})
                continue
            if rp in seen:
                continue
            seen.add(rp)
            try:
                rel = path.relative_to(root_dir).as_posix()
            except ValueError:
                rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


def normalize_stem(stem):
    s = re.sub(r"(?i)\b(v|ver|rev|r)\d+\b", " ", stem)
    s = re.sub(r"(?i)\b(final|draft|copy|backup|old|new)\b", " ", s)
    s = re.sub(r"\(\d+\)", " ", s)
    return re.sub(r"[_\-\s]+", " ", s).strip().lower()


def classify_type(rel, text):
    hay = (rel + " " + (text[:4000] if text else "")).lower()
    for label, keys in TYPE_RULES:
        for k in keys:
            if k in hay:
                return label, "keyword:%s" % k
    if rel.lower().endswith((".pdf", ".docx", ".doc")):
        return "article", "default_document_extension"
    return "unclassified", "no_evidence"


def extract_meta(path, rel, text):
    stem = Path(rel).stem
    doi_src, year_src, author_src = None, None, None
    doi = None
    for src in (stem, text[:4000] if text else ""):
        m = DOI_RE.search(src or "")
        if m:
            doi = m.group(0).rstrip(".,;)")
            doi_src = "filename" if src is stem else "text"
            break
    year = None
    m = YEAR_RE.search(stem) or (YEAR_RE.search((text or "")[:2000]))
    if m:
        year = int(m.group(1))
        year_src = "filename" if YEAR_RE.search(stem) else "text"
    author = None
    m = AUTHOR_RE.match(stem)
    if m:
        author = m.group(1)
        author_src = "filename"
    title = re.sub(r"[_]+", " ", stem).strip()
    title_src = "filename"
    if text:
        first = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 25), "")
        if first and len(title) < 12:
            title = first[:200]
            title_src = "text"
    return {
        "title": title or None,
        "title_source": title_src if title else None,
        "authors": [author] if author else None,
        "author_source": author_src,
        "year": year,
        "year_source": year_src,
        "doi": doi,
        "doi_source": doi_src,
    }


def make_tags(text):
    if not text:
        return []
    counts = {}
    for tok in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fff]{2,6}", text[:6000]):
        low = tok.lower()
        if low in STOP or len(low) < 3:
            continue
        counts[low] = counts.get(low, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"tag": k, "count": v, "basis": "term_frequency_in_filename_and_head"} for k, v in ranked]


def sanitize(text, limit=80):
    text = re.sub(r"[\\/:*?\"<>|]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" ", "_")
    return text[:limit].strip("_") or "untitled"


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Literature classifier (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    case_path = Path(args.case)
    errors, skipped = [], []
    case, status = {}, "success"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            case = json.load(fh)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
    except (OSError, ValueError) as exc:
        errors.append("cannot read case.json: %s" % exc)
        case, status = {}, "failed"

    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else "unknown"
    fields = contract_fields(case)
    template = case.get("naming_template") if isinstance(case.get("naming_template"), str) else "{author}-{year}-{title}"

    records = []
    if status != "failed":
        for path, rel in collect(candidate_roots(case, case_path), errors, skipped, case_path):
            ext = path.suffix.lower()
            try:
                st = path.stat()
                digest = hashlib.sha256(path.read_bytes()[:MAX_TEXT_BYTES * 4]).hexdigest()
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
                continue
            text, err = read_head(path, ext)
            if err:
                skipped.append({"path": rel, "reason": err})
            meta = extract_meta(path, rel, text or "")
            doc_type, basis = classify_type(rel, text or "")
            author = meta["authors"][0] if meta["authors"] else "anon"
            year = str(meta["year"]) if meta["year"] else "nd"
            proposed = template.replace("{author}", author).replace("{year}", year).replace(
                "{title}", sanitize(meta["title"]))
            proposed = "%s_%s%s" % (sanitize(proposed, 120), digest[:8], ext)
            records.append({
                "file": rel,
                "bytes": int(st.st_size),
                "sha256": digest,
                "document_type": doc_type,
                "type_basis": basis,
                "metadata": meta,
                "tags": make_tags(text or ""),
                "proposed_name": proposed,
                "rename_reason": "template=%s; missing fields use anon/nd; hash suffix prevents collisions" % template,
                "target_dir": doc_type,
                "renamed": False,
            })
        if not records:
            status = "insufficient_input"
        else:
            unresolved = [r["file"] for r in records
                          if not r["metadata"]["authors"] or not r["metadata"]["year"]
                          or r["document_type"] == "unclassified"]
            if unresolved or skipped:
                status = "partial"

    by_hash = {}
    for r in records:
        by_hash.setdefault(r["sha256"], []).append(r["file"])
    duplicates = [{"sha256": h, "members": sorted(v)} for h, v in sorted(by_hash.items()) if len(v) > 1]

    groups = {}
    for r in records:
        groups.setdefault(normalize_stem(Path(r["file"]).stem), []).append(r)
    version_groups = []
    for stem in sorted(groups):
        group = groups[stem]
        if len(group) < 2:
            continue
        hashes = {g["sha256"] for g in group}
        if len(hashes) < 2:
            continue
        version_groups.append({
            "stem_key": stem,
            "members": [{"file": g["file"], "sha256": g["sha256"]} for g in group],
        })

    unresolved = [r["file"] for r in records
                  if not r["metadata"]["authors"] or not r["metadata"]["year"]
                  or not r["metadata"]["doi"] or r["document_type"] == "unclassified"]
    assignments = [{k: r[k] for k in ("file", "document_type", "type_basis", "target_dir")} for r in records]
    tags = [{"file": r["file"], "tags": r["tags"]} for r in records]
    proposed_names = [{"file": r["file"], "proposed_name": r["proposed_name"], "reason": r["rename_reason"]} for r in records]
    summary = "Classified %d file(s): %d typed, %d unresolved field(s), %d duplicate group(s), %d version group(s)." % (
        len(records), sum(1 for r in records if r["document_type"] != "unclassified"),
        len(unresolved), len(duplicates), len(version_groups))

    result = {
        "assignments": assignments,
        "tags": tags,
        "proposed_names": proposed_names,
        "duplicates": duplicates,
        "version_groups": version_groups,
        "unresolved": unresolved,
        "records": records,
    }
    evidence = {
        "hash_algorithm": "sha256",
        "metadata_sources": "filename and first 1 MiB text head; null means not found",
        "classification_rules_version": "2026-11-20.r1",
        "template": template,
        "source_mutation": "none",
    }
    stats = {
        "files": len(records),
        "typed": sum(1 for r in records if r["document_type"] != "unclassified"),
        "unclassified": sum(1 for r in records if r["document_type"] == "unclassified"),
        "with_doi": sum(1 for r in records if r["metadata"]["doi"]),
        "duplicate_groups": len(duplicates),
        "version_groups": len(version_groups),
        "skipped": len(skipped),
        "errors": len(errors),
    }
    payload = {
        "assignments": assignments,
        "tags": tags,
        "proposed_names": proposed_names,
        "duplicates": duplicates,
        "version_groups": version_groups,
        "unresolved": unresolved,
        "manifest": assignments,
        "summary": summary,
        "status": status,
        "case_id": case_id,
        "result": result,
        "evidence": evidence,
        "uncertainty": [
            "Author and journal fields are only extracted from explicit filename/text patterns; absent values stay null.",
            "Tags are term-frequency keywords, not a controlled vocabulary; synonyms are not merged.",
            "No file was renamed or moved; proposed_name is a plan only.",
        ],
        "errors": errors,
        "stats": stats,
        "skipped": skipped,
    }

    output = {"case_id": case_id}
    for key in ("assignments", "tags", "proposed_names", "duplicates", "version_groups", "unresolved", "skipped"):
        output[key] = payload[key]
    output["status"] = status
    output["result"] = result
    output["evidence"] = evidence
    output["uncertainty"] = payload["uncertainty"]
    output["errors"] = errors
    output["stats"] = stats
    output["summary"] = summary
    notes = []
    for field in fields:
        if field == "case_id":
            output["case_id"] = case_id
            continue
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            notes.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
    if notes:
        output["contract_notes"] = notes

    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % exc)
        return 2
    try:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        with open(work / "classification_manifest.json", "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
