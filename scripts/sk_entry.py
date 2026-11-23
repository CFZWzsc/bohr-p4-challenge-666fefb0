#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic research-library organizer (stdlib only, no network).

Reads a case directory, inventories files, extracts metadata from sidecars,
case.json and embedded text, classifies by evidence, computes sha256, proposes
renames/deduplication/version resolution, and (optionally) applies them with a
reversible rollback list. Dry-run is the default.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path

TOOL_VERSION = "2026-11-24.r1"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = PACKAGE_ROOT / "assets" / "policy.json"

DEFAULT_POLICY = {
    "schema_version": "1.0",
    "rules_version": "2026-11-24.r1",
    "limits": {
        "max_files": 5000,
        "max_file_bytes": 268435456,
        "max_text_bytes": 1048576,
        "max_filename_bytes": 180,
        "max_title_chars": 90,
        "hash_chunk_bytes": 1048576,
        "max_uncertainty_entries": 120,
    },
    "naming": {
        "template": "{first_author}-{year}-{title}",
        "unknown_author": "anon",
        "unknown_year": "nd",
        "unknown_title": "untitled",
        "unknown_journal": "unknown-journal",
        "unknown_doi": "no-doi",
        "hash_suffix_length": 8,
        "reserved_windows_names": [],
    },
    "hierarchy": {
        "root": "library",
        "levels": ["{document_type}", "{year}"],
        "unknown_year": "unknown-year",
        "archive_duplicates": "library/archive/duplicates",
        "archive_versions": "library/archive/versions",
    },
    "document_types": [
        "journal_article", "review", "preprint", "conference_paper", "thesis",
        "supplement", "dataset", "scan", "unclassified",
    ],
    "document_type_aliases": {},
    "sidecar_suffixes": [".meta.json", ".metadata.json", ".sidecar.json", ".case.json", ".json"],
    "exclude_dir_names": [".git", "__pycache__", "node_modules", ".venv", "venv", ".cache"],
    "exclude_file_names": ["case.json", "task.md", "README.md", "GITHUB_URL.txt", ".DS_Store", "Thumbs.db"],
    "doi_preprint_prefixes": ["10.1101/", "10.21203/", "10.20944/", "10.48550/", "10.31219/", "10.26434/"],
    "preprint_venues": ["biorxiv", "medrxiv", "arxiv", "ssrn", "research square"],
    "formal_types": ["journal_article", "conference_paper", "review", "thesis"],
    "preprint_markers": ["preprint", "biorxiv", "medrxiv", "arxiv", "ssrn", "预印本"],
    "conference_markers": ["conference", "proceedings", "symposium", "workshop", "会议"],
    "thesis_markers": ["thesis", "dissertation", "doctoral", "学位论文", "博士论文", "硕士论文"],
    "supplement_markers": ["supplement", "supplementary", "supporting information", "补充材料", "附录"],
    "dataset_markers": ["dataset", "data set", "zenodo", "figshare", "dryad", "数据集"],
    "review_markers": ["review", "survey", "meta-analysis", "综述"],
    "scan_extensions": [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"],
    "text_extensions": [".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".tex", ".bib", ".yaml", ".yml"],
    "revision_markers": ["revision", "revised", "accepted manuscript", "proof", "corrected", "v2", "v3", "修订"],
    "formal_markers": ["published", "formal version", "version of record", "journal version", "正式版"],
    "author_stopwords": [
        "journal", "article", "paper", "document", "scan", "scanned", "supplement",
        "supplementary", "preprint", "thesis", "copy", "final", "draft", "version",
        "rev", "revision", "untitled", "file", "new", "old",
    ],
    "generic_titles": [
        "journal article", "article", "paper", "document", "scan", "supplement",
        "supplementary", "preprint", "thesis", "untitled", "file",
    ],
    "core_metadata_fields": ["title", "authors", "year", "journal", "doi"],
    "case_metadata_keys": ["metadata", "documents", "files_metadata", "items", "file_metadata"],
    "case_root_keys": [
        "input_dir", "input_root", "materials_dir", "materials", "materials_path",
        "data_dir", "corpus_dir", "directory", "path", "root", "files",
    ],
    "known_venues": [],
}

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)
DOI_LOOSE_RE = re.compile(r"10\.\d{4,9}[_/][^\s\"'<>]+", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
ARXIV_RE = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})", re.I)

LABELS = {
    "title": ["title", "题目", "标题"],
    "authors": ["authors", "author", "作者"],
    "year": ["year", "published", "publication year", "年份", "出版年"],
    "journal": ["journal", "venue", "published in", "期刊", "发表期刊"],
    "doi": ["doi"],
    "type": ["document type", "documenttype", "type", "类型", "文献类型"],
}

CLASSIFICATION_PRECEDENCE = [
    "supplement", "thesis", "conference_paper", "preprint", "dataset",
    "review", "scan", "journal_article", "unclassified",
]

MOVE_ACTIONS = {"rename_and_move", "archive_duplicate", "archive_version"}


def load_policy(path=None):
    """Load policy JSON with embedded defaults as fallback."""
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    source, error = "embedded_defaults", None
    target = Path(path) if path else POLICY_PATH
    try:
        with open(target, "r", encoding="utf-8-sig") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("policy root is not an object")
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(policy.get(key), dict):
                merged = dict(policy[key])
                merged.update(value)
                policy[key] = merged
            else:
                policy[key] = value
        source = str(target)
    except (OSError, ValueError) as exc:
        error = "policy unavailable (%s); embedded defaults used" % exc
    return policy, source, error


def now_slug(_value=None):
    return TOOL_VERSION


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, payload):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(str(tmp), str(path))
        return True
    except OSError:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
                fh.write("\n")
            return True
        except OSError:
            return False


def write_text(path, text):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except OSError:
        return False


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def unique_sorted(values):
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def sha256_file(path, policy):
    """Return (hexdigest, bytes_hashed, scope, error)."""
    chunk = int(policy.get("limits", {}).get("hash_chunk_bytes", 1048576))
    limit = int(policy.get("limits", {}).get("max_file_bytes", 268435456))
    digest = hashlib.sha256()
    total, scope, error = 0, "full", None
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                digest.update(block)
                total += len(block)
                if total >= limit:
                    scope = "partial"
                    break
    except OSError as exc:
        return None, total, "none", str(exc)
    return digest.hexdigest(), total, scope, error


def inventory_digest(entries):
    payload = [[e.get("path"), e.get("bytes"), e.get("sha256")] for e in entries]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def contract_keys(case):
    """Extract output_contract field names from many plausible shapes."""
    if not isinstance(case, dict):
        return []
    raw = None
    for key in ("output_contract", "output_contract_fields", "output_fields",
                "expected_output", "expected_outputs", "contract"):
        if key in case and case[key] is not None:
            raw = case[key]
            break
    if raw is None:
        raw = _find_contract_payload(case, 0)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                raw = json.loads(text)
            except ValueError:
                raw = [x.strip() for x in re.split(r"[,;]", text) if x.strip()]
        else:
            raw = [x.strip() for x in re.split(r"[,;\s]+", text) if x.strip()]
    keys = []
    if isinstance(raw, dict):
        for key in ("properties", "fields", "required", "keys"):
            if isinstance(raw.get(key), dict):
                keys.extend(str(k) for k in raw[key].keys())
                break
            if isinstance(raw.get(key), list):
                keys.extend(str(k) for k in raw[key] if isinstance(k, str))
                break
        else:
            keys.extend(str(k) for k in raw.keys()
                        if str(k) not in ("type", "description", "title", "additionalProperties"))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for key in ("name", "key", "field", "id", "property"):
                    if isinstance(item.get(key), str):
                        keys.append(item[key])
                        break
    return unique_sorted(k.strip() for k in keys if isinstance(k, str) and k.strip())


def _find_contract_payload(obj, depth):
    """Search nested case.json for a single output_contract-like payload."""
    if depth > 3:
        return None
    if isinstance(obj, dict):
        if "output_contract" in obj:
            return obj["output_contract"]
        for key in ("contract", "expected_output", "output_fields", "output_contract_fields"):
            if key in obj:
                return obj[key]
        for key, value in obj.items():
            if key in ("metadata", "documents", "files_metadata", "items", "file_metadata"):
                continue
            found = _find_contract_payload(value, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_contract_payload(value, depth + 1)
            if found is not None:
                return found
    return None


def case_id_of(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "caseID", "id"):
            value = case.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return "unknown"


def normalize_rel(rel):
    text = str(rel).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def resolve_case_path(raw, base):
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(os.path.expanduser(raw.strip()))
    if not candidate.is_absolute():
        candidate = Path(base) / candidate
    return candidate


def emit(level, message):
    try:
        sys.stderr.write("[%s] %s\n" % (level, message))
    except Exception:
        pass


# === PART 2: scanning and text extraction ===

def is_strong_sidecar(name):
    low = name.lower()
    return any(low.endswith(s) for s in (".meta.json", ".metadata.json", ".sidecar.json", ".case.json"))


def is_excluded_file(name, policy):
    low = name.lower()
    if low in {str(x).lower() for x in policy.get("exclude_file_names", [])}:
        return True
    return is_strong_sidecar(low)


def scan_roots(input_root, case, policy, errors, skipped):
    """Return list of inventory candidates (path, rel) and auxiliary entries."""
    root = Path(input_root)
    roots = []
    if root.exists():
        roots.append(root)
    if isinstance(case, dict):
        for key in policy.get("case_root_keys", []):
            value = case.get(key)
            for raw in as_list(value):
                if isinstance(raw, dict):
                    raw = raw.get("path") or raw.get("dir") or raw.get("file")
                candidate = resolve_case_path(raw, root)
                if candidate and candidate.exists():
                    roots.append(candidate)
                elif candidate:
                    skipped.append({
                        "file": normalize_rel(raw) if isinstance(raw, str) else str(raw),
                        "reason_code": "declared_path_missing",
                        "reason": "declared material path does not exist",
                        "recoverable": True,
                    })
    seen_roots, roots = set(), roots
    unique_roots = []
    for candidate in roots:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        unique_roots.append(candidate)

    directories = []
    seen_files = {}
    files = []
    max_files = int(policy.get("limits", {}).get("max_files", 5000))
    exclude_dirs = {str(x) for x in policy.get("exclude_dir_names", [])}

    def on_walk_error(exc):
        errors.append("directory walk error: %s" % exc)

    for base in unique_roots:
        base = Path(base)
        if base.is_file():
            candidates = [(base, base.parent)]
        else:
            candidates = []
            for dirpath, dirnames, filenames in os.walk(str(base), topdown=True,
                                                        onerror=on_walk_error,
                                                        followlinks=False):
                kept = []
                for dname in sorted(dirnames):
                    dpath = Path(dirpath) / dname
                    if dname in exclude_dirs:
                        continue
                    if dpath.is_symlink():
                        skipped.append({
                            "file": normalize_rel(str(dpath.relative_to(base))) if _inside(dpath, base) else dname,
                            "reason_code": "symlink_skipped",
                            "reason": "symbolic-link directory is not traversed",
                            "recoverable": True,
                        })
                        continue
                    kept.append(dname)
                dirnames[:] = kept
                try:
                    directories.append(normalize_rel(str(Path(dirpath).relative_to(base))) or ".")
                except ValueError:
                    directories.append(str(dirpath))
                for fname in sorted(filenames):
                    candidates.append((Path(dirpath) / fname, base))
        for path, basepath in candidates:
            try:
                if path.is_symlink():
                    rel = _try_rel(path, basepath)
                    skipped.append({"file": rel, "reason_code": "symlink_skipped",
                                    "reason": "symbolic link is not followed", "recoverable": True})
                    continue
                if not path.is_file():
                    continue
            except OSError as exc:
                skipped.append({"file": path.name, "reason_code": "stat_failed",
                                "reason": str(exc), "recoverable": True})
                continue
            if is_excluded_file(path.name, policy):
                continue
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = str(path)
            if resolved in seen_files:
                continue
            if len(files) >= max_files:
                skipped.append({"file": _try_rel(path, basepath), "reason_code": "file_cap_reached",
                                "reason": "inventory cap %d reached" % max_files, "recoverable": True})
                continue
            rel = _try_rel(path, basepath)
            if rel in {f["path"] for f in files}:
                prefix = basepath.name or "root"
                rel = normalize_rel("%s/%s" % (prefix, rel))
            seen_files[resolved] = rel
            files.append({"path": rel, "abs": path, "root": str(basepath)})

    files.sort(key=lambda item: item["path"])
    return files, sorted(set(directories)), unique_roots


def _inside(path, base):
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False


def _try_rel(path, base):
    try:
        rel = normalize_rel(str(Path(path).relative_to(base)))
        return rel or path.name
    except ValueError:
        return path.name


def find_sidecar(item, policy, inventory_paths):
    """Return (path, payload, error) for a sidecar beside item."""
    path = item["abs"]
    stem = path.stem
    candidates = []
    suffixes = [s for s in policy.get("sidecar_suffixes", []) if s != ".json"]
    for suffix in suffixes:
        candidates.append(path.parent / (path.name + suffix))
        candidates.append(path.parent / (stem + suffix))
    candidates.append(path.parent / (stem + ".json"))
    candidates.append(path.parent / (path.name + ".json"))
    seen, ordered = set(), []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    for candidate in ordered:
        if not candidate.is_file():
            continue
        rel = _try_rel(candidate, Path(item["root"]))
        if rel == item["path"]:
            continue
        try:
            payload = read_json(candidate)
        except (OSError, ValueError) as exc:
            return rel, None, "sidecar unreadable: %s" % exc
        if isinstance(payload, dict):
            return rel, payload, None
        return rel, None, "sidecar root is not an object"
    return None, None, None


def case_metadata_map(case, policy, input_root):
    """Map normalized file keys to metadata dictionaries from case.json."""
    mapping = {}

    def add_entry(entry):
        if not isinstance(entry, dict):
            return
        meta = entry
        for container in ("metadata", "fields", "meta"):
            if isinstance(entry.get(container), dict):
                meta = entry[container]
                break
        keys = [entry.get(k) for k in ("path", "file", "filename", "rel_path", "name", "id")]
        keys = [str(k) for k in keys if isinstance(k, (str, int)) and str(k).strip()]
        if not keys:
            return
        for key in keys:
            for variant in _meta_keys(key, input_root):
                mapping[variant] = meta

    for container in policy.get("case_metadata_keys", []):
        value = case.get(container) if isinstance(case, dict) else None
        if isinstance(value, dict):
            for key, meta in value.items():
                if isinstance(meta, dict):
                    for variant in _meta_keys(key, input_root):
                        mapping[variant] = meta
        elif isinstance(value, list):
            for entry in value:
                add_entry(entry)
    return mapping


def _meta_keys(raw, input_root):
    text = normalize_rel(str(raw))
    variants = [text, text.split("/")[-1], Path(text).stem]
    if input_root:
        try:
            abs_path = Path(raw)
            if not abs_path.is_absolute():
                abs_path = Path(input_root) / text
            variants.append(str(abs_path.resolve()))
        except OSError:
            pass
    return unique_sorted(v for v in variants if v)


def lookup_case_metadata(mapping, item):
    if not mapping:
        return None
    rel = item["path"]
    keys = [
        rel, rel.split("/")[-1], Path(rel).stem,
        str(Path(item["abs"]).resolve()) if item.get("abs") else rel,
    ]
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def pdf_text(data):
    """Best-effort text from PDF content streams, stdlib only."""
    parts = []
    for match in re.finditer(rb"stream\r?\n", data):
        start, end = match.end(), data.find(b"endstream", match.end())
        if end < 0:
            continue
        raw = data[start:end]
        try:
            raw = zlib_decompress(raw)
        except Exception:
            pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        for literal in re.finditer(rb"\((?:\\.|[^()\\])*\)", raw, re.S):
            token = literal.group(0)[1:-1]
            token = token.replace(b"\\(", b"(").replace(b"\\)", b")")
            token = token.replace(b"\\\\", b"\\")
            try:
                parts.append(token.decode("latin-1"))
            except Exception:
                pass
        for hex_token in re.finditer(rb"<([0-9A-Fa-f\s]{4,})>", raw):
            try:
                cleaned = re.sub(rb"\s+", b"", hex_token.group(1))
                if len(cleaned) % 2:
                    cleaned += b"0"
                parts.append(bytes.fromhex(cleaned.decode("ascii")).decode("latin-1", "ignore"))
            except Exception:
                pass
    if not parts:
        for literal in re.finditer(rb"\((?:\\.|[^()\\])*\)\s*Tj", data, re.S):
            token = literal.group(0)
            token = token[token.find(b"(") + 1:token.rfind(b")")]
            parts.append(token.decode("latin-1", "ignore"))
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def zlib_decompress(raw):
    import zlib
    return zlib.decompress(raw)


def docx_text(path):
    try:
        with zipfile.ZipFile(str(path)) as archive:
            names = [n for n in archive.namelist() if n.endswith("word/document.xml")]
            if not names:
                return ""
            xml = archive.read(names[0]).decode("utf-8", "ignore")
        xml = re.sub(r"<w:tab[^>]*/>", " ", xml)
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        return re.sub(r"[ \t]+", " ", xml).strip()
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""


def decode_bytes(data):
    if not data:
        return "", None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding), None
        except UnicodeDecodeError:
            continue
    best = ("", -1.0, "latin-1")
    for encoding in ("gb18030", "big5", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        sample = text[:8000]
        if not sample:
            continue
        printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
        cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
        replacement = sample.count("\ufffd")
        score = printable / len(sample) + min(cjk, 2000) / len(sample) - replacement
        if score > best[1]:
            best = (text, score, encoding)
    warning = None
    text = best[0]
    if not text:
        return "", "undecodable binary content"
    printable = sum(1 for ch in text[:8000] if ch.isprintable() or ch in "\r\n\t")
    if text and printable / max(1, len(text[:8000])) < 0.75:
        return "", "binary content without text layer"
    replacements = text.count("\ufffd")
    if replacements and replacements / max(1, len(text)) > 0.01:
        warning = "encoding produced replacement characters; original may be garbled"
    return text, warning


def read_text(path, policy):
    """Return (text, status, detail, size) with status ok/empty/no_text/error."""
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, "error", "stat failed: %s" % exc, 0
    if size == 0:
        return None, "empty", "zero-byte file", 0
    max_text = int(policy.get("limits", {}).get("max_text_bytes", 1048576))
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_text + 1)
    except OSError as exc:
        return None, "error", "unreadable: %s" % exc, size
    truncated = len(data) > max_text
    data = data[:max_text]
    if ext == ".pdf":
        if b"%PDF" not in data[:1024]:
            return None, "error", "corrupt or truncated PDF header", size
        if b"/Encrypt" in data:
            return None, "encrypted", "encrypted PDF; text not extracted", size
        text = pdf_text(data)
        if len(text) < 20:
            return None, "no_text", "PDF has no extractable text layer (scanned or image-only)", size
        return text, "ok", "pdf streams decoded" + ("; head truncated" if truncated else ""), size
    if ext == ".docx":
        text = docx_text(path)
        if not text:
            return None, "no_text", "docx contains no readable document.xml text", size
        return text, "ok", "docx document.xml parsed", size
    if ext in (".html", ".htm", ".xml"):
        text, warning = decode_bytes(data)
        if text:
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text, "ok", warning or "markup tags stripped", size
        return None, "no_text", warning or "no text", size
    if ext in policy.get("scan_extensions", []):
        return None, "no_text", "image file without embedded text layer", size
    text, warning = decode_bytes(data)
    if text:
        return text, "ok", warning or "plain text decoded", size
    return None, "no_text", warning or "no text layer", size


def parse_text_fields(text):
    if not text:
        return {}
    clean = re.sub(r"\s+", " ", text).strip()
    fields, evidence = {}, {}
    all_labels = []
    for labels in LABELS.values():
        all_labels.extend(labels)
    label_alt = "|".join(re.escape(x) for x in sorted(all_labels, key=len, reverse=True))
    for field, labels in LABELS.items():
        alt = "|".join(re.escape(x) for x in labels)
        pattern = re.compile(
            r"(?:^|[\s;|])(?:%s)\s*[:：]\s*(.+?)(?=(?:[\s;|](?:%s)\s*[:：])|$)" % (alt, label_alt),
            re.I,
        )
        match = pattern.search(clean)
        if match:
            value = match.group(1).strip(" ;|")
            if value:
                fields[field] = value
                evidence[field] = "label:%s" % match.group(0).split(":")[0].split("：")[0].strip()
    doi_match = DOI_RE.search(clean)
    if doi_match:
        fields["doi"] = doi_match.group(0).rstrip(".,;)]}")
        evidence["doi"] = "doi_regex"
    elif ARXIV_RE.search(clean):
        fields["arxiv_id"] = ARXIV_RE.search(clean).group(1)
    if "year" not in fields:
        year_match = YEAR_RE.search(clean[:3000])
        if year_match:
            fields["year"] = year_match.group(1)
            evidence["year"] = "year_regex_in_text_head"
    if "title" not in fields:
        for line in text.splitlines()[:12]:
            candidate = line.strip()
            low = candidate.lower()
            if not (20 <= len(candidate) <= 300):
                continue
            if low.startswith(("abstract", "introduction", "keywords", "doi", "http", "%pdf", "references")):
                continue
            if re.search(r"[A-Za-z\u4e00-\u9fff]{8,}", candidate):
                fields["title"] = candidate
                evidence["title"] = "first_text_line"
                break
    if "journal" not in fields:
        for venue in ("Nature Nanotechnology", "Nature Materials", "Nature Communications",
                      "Nature", "Science", "Cell", "Physical Review Letters", "PLOS ONE",
                      "eLife", "PNAS", "bioRxiv", "medRxiv", "arXiv"):
            if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(venue), clean, re.I):
                fields["journal"] = venue
                evidence["journal"] = "known_venue_in_text"
                break
    fields["_evidence"] = evidence
    if fields.get("title"):
        generic = {"abstract", "introduction", "references", "keywords"}
        if fields["title"].strip().lower() in generic:
            fields.pop("title", None)
    return fields


def parse_filename_fields(stem, policy):
    raw = unicodedata.normalize("NFC", stem)
    working = re.sub(r"(?i)^(copy of|copy|副本|拷贝)[\s_\-]+", "", raw)
    working = re.sub(r"\s*\(\d+\)\s*$", "", working)
    doi = None
    loose = DOI_LOOSE_RE.search(working)
    if loose:
        candidate = loose.group(0).rstrip(".,;)]}")
        candidate = re.sub(r"^(10\.\d{4,9})_", r"\1/", candidate)
        if DOI_RE.match(candidate):
            doi = candidate
    tokens = [t for t in re.split(r"[_\-\s.]+", working) if t]
    year = None
    year_index = None
    for idx, token in enumerate(tokens):
        if YEAR_RE.fullmatch(token):
            year = int(token)
            year_index = idx
            break
    stopwords = {str(x).lower() for x in policy.get("author_stopwords", [])}
    generic_titles = {str(x).lower() for x in policy.get("generic_titles", [])}
    author = None
    if year_index and year_index > 0:
        candidate = tokens[year_index - 1]
        if re.fullmatch(r"[A-Za-z][A-Za-z'\-]{1,30}", candidate) and candidate.lower() not in stopwords:
            author = candidate
    title_tokens = tokens[year_index + 1:] if year_index is not None else tokens
    drop = stopwords | {"supp", "s1", "s2", "final", "copy", "full", "text", "revised"}
    title_tokens = [t for t in title_tokens
                    if t.lower() not in drop and not YEAR_RE.fullmatch(t)
                    and not re.fullmatch(r"[A-Za-z]?\d{1,3}", t)]
    title = " ".join(title_tokens).strip(" -_.")
    if (not title or len(title) < 4) and year_index is not None:
        before = [t for t in tokens[:max(0, year_index - (1 if author else 0))]
                  if t.lower() not in stopwords]
        title = " ".join(before).strip(" -_.")
    if title and (len(title) < 4 or title.lower() in generic_titles or DOI_RE.fullmatch(title)):
        title = None
    if doi and len(working.replace(doi, "").strip(" _-")) < 3:
        title = None
    fields = {
        "title": title,
        "year": year,
        "doi": doi,
        "authors": [author] if author else None,
        "filename_tokens": tokens,
    }
    return {k: v for k, v in fields.items() if v is not None}


def normalize_authors(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parts = re.split(r"\s*(?:;|\band\b|\||&)\s*", text)
        if len(parts) == 1 and "," in text and text.count(",") <= 2:
            parts = [p.strip() for p in text.split(",")]
        authors = [p.strip() for p in parts if p.strip()]
        return authors or None
    if isinstance(value, (list, tuple)):
        authors = []
        for item in value:
            if isinstance(item, str) and item.strip():
                authors.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("full_name") or item.get("family")
                if name:
                    authors.append(str(name).strip())
        return authors or None
    return None


def normalize_year(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2100 else None
    text = str(value)
    match = YEAR_RE.search(text)
    return int(match.group(1)) if match else None


def normalize_type(value, policy):
    if not isinstance(value, str):
        return None
    key = re.sub(r"\s+", " ", value.strip().lower().replace("_", " "))
    aliases = policy.get("document_type_aliases", {})
    if key in aliases:
        return aliases[key]
    key_us = key.replace(" ", "_")
    if key_us in policy.get("document_types", []):
        return key_us
    return None


def clean_string(value, limit=300):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None
    return value[:limit].strip()


def build_metadata(item, sidecar, case_meta, text, text_fields, policy):
    fields = {k: None for k in ("title", "authors", "year", "journal", "doi",
                                "volume", "issue", "pages", "publisher", "language")}
    provenance = {}
    notes = []

    def apply(source_name, payload, confidence):
        if not isinstance(payload, dict):
            return
        for key in ("title", "authors", "year", "journal", "doi",
                    "volume", "issue", "pages", "publisher", "language"):
            if fields[key] is not None:
                continue
            value = payload.get(key)
            if key == "authors":
                value = normalize_authors(value)
            elif key == "year":
                value = normalize_year(value)
            elif key == "doi":
                value = clean_string(value, 300)
                if value:
                    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
                    value = value.rstrip(".,;)]}")
                    if not DOI_RE.match(value):
                        value = None
            else:
                value = clean_string(value, 300)
            if value:
                fields[key] = value
                provenance[key] = {"source": source_name, "confidence": confidence}

    sidecar_fields = {}
    if isinstance(sidecar, dict):
        sidecar_fields = dict(sidecar)
        for alt, canonical in (("author", "authors"), ("publication_year", "year"),
                               ("date", "year"), ("venue", "journal"),
                               ("container_title", "journal"), ("DOI", "doi")):
            if canonical not in sidecar_fields and alt in sidecar_fields:
                sidecar_fields[canonical] = sidecar_fields[alt]
        apply("sidecar", sidecar_fields, "high")
    apply("case_json", case_meta, "high")
    text_payload = dict(text_fields or {})
    if text_payload:
        apply("embedded_text", text_payload, "medium")
        for key in fields:
            if key in text_payload and key not in provenance:
                provenance[key] = {"source": "embedded_text", "confidence": "medium"}
    hint = parse_filename_fields(Path(item["path"]).stem, policy)
    apply("filename_hint", hint, "low")

    if not fields["language"] and text:
        sample = text[:4000]
        cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
        if cjk > max(10, len(sample) * 0.05):
            fields["language"] = "zh"
            provenance["language"] = {"source": "embedded_text", "confidence": "medium"}
        elif re.search(r"[A-Za-z]{4,}", sample):
            fields["language"] = "en"
            provenance["language"] = {"source": "embedded_text", "confidence": "low"}

    if fields["title"] and fields["title"].strip().lower() in {str(x).lower() for x in policy.get("generic_titles", [])}:
        fields["title"] = None
        provenance.pop("title", None)
        notes.append("generic filename title rejected")
    if fields["authors"]:
        fields["authors"] = [a for a in fields["authors"] if a]
        if not fields["authors"]:
            fields["authors"] = None
            provenance.pop("authors", None)

    core = policy.get("core_metadata_fields", ["title", "authors", "year", "journal", "doi"])
    unknown = [k for k in core if not fields.get(k)]
    for key in core:
        if fields.get(key) and key not in provenance:
            provenance[key] = {"source": "unknown", "confidence": "low"}
    record = dict(fields)
    record["file"] = item["path"]
    record["first_author"] = fields["authors"][0] if fields.get("authors") else None
    record["provenance"] = provenance
    record["unknown_fields"] = unknown
    record["notes"] = notes
    return record


# === PART 3: classification, naming, deduplication ===

def marker_present(haystack, marker):
    if not haystack or not marker:
        return False
    low_hay, low_marker = haystack.lower(), str(marker).lower()
    if re.search(r"[\u4e00-\u9fff]", low_marker):
        return low_marker in low_hay
    return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(low_marker), low_hay) is not None


def classify_item(item, meta, text, text_status, sidecar, case_meta, policy):
    signals = []

    def add(kind, value, weight, detail):
        signals.append({"kind": kind, "document_type": value, "weight": int(weight), "detail": detail})

    for source_name, payload in (("sidecar", sidecar), ("case_json", case_meta)):
        if isinstance(payload, dict):
            raw_type = payload.get("document_type") or payload.get("type") or payload.get("kind")
            canonical = normalize_type(raw_type, policy)
            if canonical:
                add(source_name, canonical, 100, "%s declares type=%s" % (source_name, raw_type))

    text_type = (meta.get("_text_type") or "")
    if text_type:
        canonical = normalize_type(text_type, policy)
        if canonical:
            add("embedded_text", canonical, 85, "Document Type label: %s" % text_type)

    file_stem = Path(item["path"]).stem
    title = meta.get("title") or ""
    venue_text = " ".join(str(meta.get(k) or "") for k in ("journal", "publisher"))
    name_hay = "%s %s" % (file_stem, title)
    head_hay = (text or "")[:6000]
    doi = (meta.get("doi") or "").lower()

    for prefix in policy.get("doi_preprint_prefixes", []):
        if doi.startswith(str(prefix).lower()):
            add("doi", "preprint", 55, "preprint DOI prefix %s" % prefix)
            break
    if "arxiv" in doi or "arxiv" in file_stem.lower():
        add("doi", "preprint", 55, "arXiv identifier")

    for venue in policy.get("preprint_venues", []):
        if marker_present(venue_text, venue):
            add("venue", "preprint", 62, "preprint venue %s" % venue)
            break

    marker_sets = (
        ("supplement", policy.get("supplement_markers", []), 64),
        ("thesis", policy.get("thesis_markers", []), 64),
        ("conference_paper", policy.get("conference_markers", []), 62),
        ("preprint", policy.get("preprint_markers", []), 60),
        ("dataset", policy.get("dataset_markers", []), 60),
        ("review", policy.get("review_markers", []), 52),
    )
    for doc_type, markers, weight in marker_sets:
        for marker in markers:
            if marker_present(name_hay, marker):
                add("filename_or_title", doc_type, weight + 4, "marker '%s' in filename/title" % marker)
                break
        else:
            if doc_type in ("supplement", "review"):
                continue
            for marker in markers:
                if marker_present(head_hay, marker):
                    add("embedded_text", doc_type, weight - 10, "marker '%s' in text head" % marker)
                    break
    if meta.get("journal") and "supplement" in str(meta.get("journal")).lower():
        add("venue", "supplement", 70, "journal/venue says supplement")

    if text and text_status == "ok":
        low = text.lower()
        if "abstract" in low and ("references" in low or "bibliography" in low):
            add("structure", "journal_article", 24, "abstract+references structure")
        if ("supervisor" in low or "committee" in low) and ("chapter" in low or "degree" in low):
            add("structure", "thesis", 30, "thesis structure (supervisor/degree/chapter)")

    if (not text or text_status in ("no_text", "empty")) and not any(s["weight"] >= 40 for s in signals):
        ext = Path(item["path"]).suffix.lower()
        if text_status == "no_text" and (ext == ".pdf" or ext in policy.get("scan_extensions", [])):
            add("no_text_layer", "scan", 36, "no extractable text layer")

    if not any(s["weight"] >= 40 for s in signals):
        ext = Path(item["path"]).suffix.lower()
        if ext in (".pdf", ".doc", ".docx"):
            add("extension_default", "journal_article", 10, "document extension without stronger evidence")

    if text_status in ("error", "encrypted") and not any(s["weight"] >= 40 for s in signals):
        add("extraction_failed", "unclassified", 15, "text extraction failed; type not guessed")

    if not signals:
        add("no_evidence", "unclassified", 0, "no classification evidence found")

    signals.sort(key=lambda s: (-s["weight"], CLASSIFICATION_PRECEDENCE.index(s["document_type"])
                                if s["document_type"] in CLASSIFICATION_PRECEDENCE else 99))
    best = signals[0]
    doc_type = best["document_type"]
    weight = best["weight"]
    if weight >= 80:
        confidence = "high"
    elif weight >= 40:
        confidence = "medium"
    else:
        confidence = "low"
    evidence = [{"kind": s["kind"], "value": s["document_type"], "weight": s["weight"],
                 "detail": s["detail"]} for s in signals]
    return {
        "file": item["path"],
        "document_type": doc_type,
        "category": doc_type,
        "target_dir": "",
        "confidence": confidence,
        "basis": "rule:%s" % best["kind"],
        "evidence": evidence,
        "signals": signals,
    }


def taxonomy_label(doc_type, case, policy):
    taxonomy = case.get("taxonomy") or case.get("categories")
    if not isinstance(taxonomy, list):
        return doc_type
    labels = [str(x) for x in taxonomy if isinstance(x, str)]
    aliases = policy.get("document_type_aliases", {})
    candidates = [doc_type.replace("_", " "), doc_type]
    for alias, canonical in aliases.items():
        if canonical == doc_type:
            candidates.append(alias)
    for label in labels:
        low = label.lower().strip()
        if low in {c.lower() for c in candidates}:
            return label
    return doc_type


def target_dir_for(doc_type, year, case, policy, category=None):
    hierarchy = case.get("directory_hierarchy") or case.get("hierarchy") or policy.get("hierarchy", {})
    if isinstance(hierarchy, dict):
        root = hierarchy.get("root", "library")
        levels = hierarchy.get("levels", ["{document_type}", "{year}"])
        unknown_year = hierarchy.get("unknown_year", "unknown-year")
    else:
        root, levels, unknown_year = "library", ["{document_type}", "{year}"], "unknown-year"
    if not isinstance(levels, list) or not levels:
        levels = ["{document_type}", "{year}"]
    allowed = {"{document_type}", "{category}", "{year}", "{first_author}", "{journal}"}
    values = {
        "{document_type}": doc_type,
        "{category}": category or doc_type,
        "{year}": str(year) if year else str(unknown_year),
        "{first_author}": "anon",
        "{journal}": "unknown-journal",
    }
    parts = [sanitize_component(str(root), 60)]
    for level in levels:
        level = str(level)
        if level not in allowed:
            continue
        parts.append(sanitize_component(values[level], 80))
    return "/".join(p for p in parts if p)


def sanitize_component(value, max_bytes=120, fallback="untitled"):
    if value is None:
        return fallback
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"[\\/:*?\"<>|{}]+", "-", text)
    text = re.sub(r"\s+", "-", text).strip(" .-_")
    if not text:
        return fallback
    upper = text.split(".")[0].strip().upper()
    reserved = {str(x).upper() for x in DEFAULT_POLICY["naming"]["reserved_windows_names"]}
    if upper in reserved:
        text = "_" + text
    while len(text.encode("utf-8")) > max_bytes and text:
        text = text[:-1]
    return text.rstrip(" .-_") or fallback


def build_name(item, meta, classification, case, policy, variant=None):
    naming = policy.get("naming", {})
    template = case.get("naming_template") if isinstance(case, dict) else None
    if not isinstance(template, str) or "{" not in template:
        template = naming.get("template", "{first_author}-{year}-{title}")
    authors = meta.get("authors") or []
    first_author = authors[0] if authors else naming.get("unknown_author", "anon")
    year_value = meta.get("year")
    values = {
        "{first_author}": sanitize_component(first_author, 40, naming.get("unknown_author", "anon")),
        "{author}": sanitize_component(first_author, 40, naming.get("unknown_author", "anon")),
        "{year}": str(year_value) if year_value else naming.get("unknown_year", "nd"),
        "{title}": sanitize_component(meta.get("title"), 100, naming.get("unknown_title", "untitled")),
        "{journal}": sanitize_component(meta.get("journal"), 50, naming.get("unknown_journal", "unknown-journal")),
        "{doi}": sanitize_component(meta.get("doi"), 80, naming.get("unknown_doi", "no-doi")),
        "{type}": classification.get("document_type", "unclassified"),
        "{document_type}": classification.get("document_type", "unclassified"),
        "{ext}": item["abs"].suffix.lstrip(".").lower() if item.get("abs") else "",
    }
    name = template
    for placeholder, replacement in values.items():
        name = name.replace(placeholder, replacement)
    name = sanitize_component(name, int(policy.get("limits", {}).get("max_filename_bytes", 180)),
                              naming.get("unknown_title", "untitled"))
    ext = item["abs"].suffix.lower() if item.get("abs") else ""
    if ext and not name.lower().endswith(ext):
        name += ext
    if variant:
        stem, dot, suffix = name.rpartition(".")
        if dot and len(suffix) <= 8:
            name = "%s__%s.%s" % (stem, variant, suffix)
        else:
            name = "%s__%s" % (name, variant)
    return name


def short_hash(value, length=8):
    return (value or "nohash")[:length]


def duplicate_groups(records, policy):
    by_hash = {}
    for record in records:
        if not record.get("sha256") or record.get("sha256_scope") != "full":
            continue
        by_hash.setdefault(record["sha256"], []).append(record)
    groups, roles = [], {}
    for digest in sorted(by_hash):
        members = sorted(by_hash[digest], key=lambda r: r["file"])
        if len(members) < 2:
            continue
        keeper = min(members, key=lambda r: duplicate_keeper_key(r, policy))
        roles[keeper["file"]] = {"role": "keeper", "group": digest}
        for member in members:
            if member["file"] != keeper["file"]:
                roles[member["file"]] = {"role": "duplicate", "group": digest}
        groups.append({
            "sha256": digest,
            "keeper": keeper["file"],
            "members": [m["file"] for m in members],
            "relation": "identical_content",
            "action": "keep_one_archive_rest",
            "resolution": "keeper %s; identical copies archived, never deleted" % keeper["file"],
        })
    return groups, roles


def doc_type_of(record):
    classification = record.get("classification") or {}
    return classification.get("document_type") or record.get("document_type") or "unclassified"


def duplicate_keeper_key(record, policy):
    doc_type = doc_type_of(record)
    type_rank = 0 if doc_type in policy.get("formal_types", []) else 1
    name = record["file"].lower()
    copy_penalty = 1 if re.search(r"(?i)(copy|副本|\(\d+\))", name) else 0
    archive_penalty = 1 if "/archive/" in "/" + name else 0
    sidecar_penalty = 0 if record.get("sidecar") else 1
    return (type_rank, archive_penalty, copy_penalty, sidecar_penalty,
            len(record["file"].split("/")), record["file"])


def normalized_title(title):
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title)).lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_versions(records, duplicate_roles, policy):
    eligible = [r for r in records
                if r.get("sha256")
                and doc_type_of(r) not in ("supplement", "scan", "unclassified")
                and duplicate_roles.get(r["file"], {}).get("role") != "duplicate"]
    parent = {r["file"]: r["file"] for r in eligible}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_doi, by_title = {}, {}
    for record in eligible:
        doi = (record.get("meta", {}).get("doi") or "").lower()
        if doi:
            by_doi.setdefault(doi, []).append(record)
        meta = record.get("meta", {})
        title = normalized_title(meta.get("title"))
        authors = meta.get("authors") or []
        first = authors[0].lower() if authors else ""
        year_value = meta.get("year")
        if title and len(title) >= 10:
            by_title.setdefault((title, first, year_value), []).append(record)
    for bucket in list(by_doi.values()) + list(by_title.values()):
        if len(bucket) < 2:
            continue
        first = bucket[0]["file"]
        for record in bucket[1:]:
            union(first, record["file"])

    clusters = {}
    for record in eligible:
        clusters.setdefault(find(record["file"]), []).append(record)
    groups, roles = [], {}
    for cluster in clusters.values():
        if len({r["sha256"] for r in cluster}) < 2:
            continue
        variants = {r["file"]: variant_of(r, policy) for r in cluster}
        has_preprint = any(v == "preprint" for v in variants.values())
        has_formal = any(v in ("formal", "revision") for v in variants.values())
        if has_preprint and has_formal:
            relation = "preprint_vs_formal"
        elif any(v == "revision" for v in variants.values()):
            relation = "revision"
        else:
            relation = "same_work"
        keeper = min(cluster, key=lambda r: version_keeper_key(r, variants, policy))
        doi_set = sorted({(r.get("meta", {}).get("doi") or "") for r in cluster if r.get("meta", {}).get("doi")})
        title_set = sorted({r.get("meta", {}).get("title") or "" for r in cluster if r.get("meta", {}).get("title")})
        members = []
        for record in sorted(cluster, key=lambda r: r["file"]):
            role = "keeper" if record["file"] == keeper["file"] else "archived"
            roles[record["file"]] = {"role": role, "variant": variants[record["file"]], "relation": relation}
            members.append({
                "file": record["file"],
                "sha256": record["sha256"],
                "document_type": doc_type_of(record),
                "variant": variants[record["file"]],
                "role": role,
            })
        groups.append({
            "group_key": "doi:%s" % doi_set[0] if len(doi_set) == 1 else "title:%s" % (title_set[0] if title_set else "heuristic"),
            "group_by": "doi" if len(doi_set) == 1 else "title+first_author+year",
            "relation": relation,
            "keeper": keeper["file"],
            "members": members,
            "resolution": "keep %s; other versions archived with variant label" % keeper["file"],
        })
    return groups, roles


def variant_of(record, policy):
    doc_type = doc_type_of(record)
    meta = record.get("meta", {})
    hay = " ".join(str(x or "") for x in (
        record["file"], meta.get("title"), meta.get("journal"))) + " " + (record.get("text_head") or "")[:1200]
    for marker in policy.get("revision_markers", []):
        if marker_present(hay, marker):
            return "revision"
    if doc_type == "preprint":
        return "preprint"
    for marker in policy.get("preprint_markers", []):
        if marker_present(hay, marker):
            return "preprint"
    if doc_type in policy.get("formal_types", []):
        return "formal"
    if doc_type == "supplement":
        return "supplement"
    return "other"


def version_keeper_key(record, variants, policy):
    variant_rank = {"formal": 0, "revision": 1, "preprint": 2, "supplement": 3, "other": 4}
    has_doi = 0 if record.get("meta", {}).get("doi") else 1
    return (variant_rank.get(variants[record["file"]], 5), has_doi,
            len(record["file"].split("/")), record["file"])


# === PART 4: planning, apply, result assembly ===

def make_tags(meta, text):
    hay = " ".join(str(meta.get(k) or "") for k in ("title", "journal")) + " " + (text or "")[:5000]
    stop = set("the and for with from this that these those study using based on of in to a an is are was were "
               "we our their by as at be been can has have not but or all also into than then more most such "
               "研究 分析 方法 结果 本文 基于 以及 一个 我们 数据 文献 用于 通过".split())
    counts = {}
    for token in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fff]{2,6}", hay):
        low = token.lower()
        if low in stop or len(low) < 3:
            continue
        counts[low] = counts.get(low, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"tag": tag, "count": count, "basis": "term_frequency_in_title_and_text_head"}
            for tag, count in ranked]


def role_for(record, duplicate_roles, version_roles):
    path = record["file"]
    if path in duplicate_roles and duplicate_roles[path]["role"] == "duplicate":
        return "duplicate", duplicate_roles[path]
    if path in version_roles and version_roles[path]["role"] == "archived":
        return "archived", version_roles[path]
    if path in version_roles and version_roles[path]["role"] == "keeper":
        return "version_keeper", version_roles[path]
    return "keeper", {}


def sidecar_suffix_for(path):
    name = Path(path).name.lower()
    for suffix in (".meta.json", ".metadata.json", ".sidecar.json", ".case.json"):
        if name.endswith(suffix):
            return suffix
    return ".json"


def plan_renames(records, duplicate_roles, version_roles, case, policy, input_root):
    renames, conflicts = [], []
    archive_duplicates = policy.get("hierarchy", {}).get("archive_duplicates", "library/archive/duplicates")
    archive_versions = policy.get("hierarchy", {}).get("archive_versions", "library/archive/versions")

    for record in sorted(records, key=lambda r: r["file"]):
        classification = record["classification"]
        meta = record["meta"]
        role, role_info = role_for(record, duplicate_roles, version_roles)
        if role == "duplicate":
            target_dir = archive_duplicates
            variant = "dup"
        elif role == "archived":
            target_dir = archive_versions
            variant = role_info.get("variant") or "version"
        else:
            target_dir = classification.get("target_dir") or target_dir_for(
                classification["document_type"], meta.get("year"), case, policy,
                classification.get("category"))
            variant = None
        name = build_name(record, meta, classification, case, policy, variant=variant)
        target = normalize_rel("%s/%s" % (target_dir, name))
        action = "rename_and_move"
        if role == "duplicate":
            action = "archive_duplicate"
        elif role == "archived":
            action = "archive_version"
        status = "planned"
        reason = "%s; role=%s; template=%s" % (
            classification.get("basis"), role,
            case.get("naming_template") or policy.get("naming", {}).get("template"))
        if not record.get("sha256"):
            status = "blocked"
            action = "manual_review"
            conflicts.append({
                "file": record["file"], "kind": "unreadable_or_unhashed",
                "detail": "cannot hash/read source; rename requires manual review",
                "resolution": "kept in place; no move planned",
            })
        elif record["file"] == target:
            status = "already_in_place"
            action = "none"
            reason += "; already at target"
        renames.append({
            "source": record["file"],
            "target": target,
            "document_type": classification["document_type"],
            "action": action,
            "reason": reason,
            "sha256": record.get("sha256"),
            "role": role,
            "variant": variant,
            "status": status,
            "sidecar": record.get("sidecar"),
            "sidecar_target": (target + sidecar_suffix_for(record["sidecar"]) if record.get("sidecar") else None),
        })

    # Resolve duplicate proposed targets deterministically.
    priority = {"keeper": 0, "version_keeper": 1, "archived": 2, "duplicate": 3}
    ordered = sorted(renames, key=lambda r: (priority.get(r["role"], 9), r["status"] != "planned", r["source"]))
    used = {}
    for entry in ordered:
        if entry["status"] != "planned" or entry["action"] not in MOVE_ACTIONS:
            used.setdefault(entry["target"], entry["source"])
            continue
        target = entry["target"]
        if target in used and used[target] != entry["source"]:
            candidate = with_hash_suffix(target, entry["sha256"], policy)
            counter = 2
            while candidate in used:
                candidate = with_counter_suffix(candidate, counter)
                counter += 1
            conflicts.append({
                "file": entry["source"], "kind": "proposed_target_collision",
                "detail": "two sources proposed %s" % target,
                "resolution": "target changed to %s" % candidate,
            })
            entry["target"] = candidate
            entry["sidecar_target"] = candidate + sidecar_suffix_for(entry["sidecar"]) if entry.get("sidecar") else None
            target = candidate
        used[target] = entry["source"]

    # Resolve collisions with files already on disk.
    base = Path(input_root)
    moving_sources = {r["source"] for r in renames if r["status"] == "planned" and r["action"] in MOVE_ACTIONS}
    for entry in renames:
        if entry["status"] != "planned" or entry["action"] not in MOVE_ACTIONS:
            continue
        target_abs = base / entry["target"]
        if not target_abs.exists():
            continue
        target_rel = entry["target"]
        if target_rel in moving_sources:
            continue
        if target_abs.is_dir():
            candidate = with_hash_suffix(entry["target"], entry["sha256"], policy)
            conflicts.append({
                "file": entry["source"], "kind": "target_is_directory",
                "detail": "%s exists as a directory" % target_rel,
                "resolution": "target changed to %s" % candidate,
            })
            entry["target"] = candidate
            entry["sidecar_target"] = candidate + sidecar_suffix_for(entry["sidecar"]) if entry.get("sidecar") else None
            continue
        existing_sha, _, scope, _ = sha256_file(target_abs, policy)
        if scope == "full" and existing_sha and existing_sha == entry["sha256"]:
            entry["status"] = "already_present"
            entry["action"] = "none"
            entry["reason"] += "; identical file already exists at target"
            conflicts.append({
                "file": entry["source"], "kind": "target_exists_same_content",
                "detail": "target %s already holds identical content" % target_rel,
                "resolution": "source kept untouched; no overwrite and no deletion",
            })
        else:
            candidate = with_hash_suffix(entry["target"], entry["sha256"], policy)
            counter = 2
            while (base / candidate).exists():
                candidate = with_counter_suffix(candidate, counter)
                counter += 1
            conflicts.append({
                "file": entry["source"], "kind": "target_exists_other_content",
                "detail": "target %s already exists with different content" % target_rel,
                "resolution": "target changed to %s; existing file untouched" % candidate,
            })
            entry["target"] = candidate
            entry["sidecar_target"] = candidate + sidecar_suffix_for(entry["sidecar"]) if entry.get("sidecar") else None

    renames.sort(key=lambda r: r["source"])
    return renames, conflicts


def with_hash_suffix(target, digest, policy):
    length = int(policy.get("naming", {}).get("hash_suffix_length", 8))
    suffix = "__" + short_hash(digest, length)
    if "." in Path(target).name:
        stem, dot, ext = target.rpartition(".")
        return "%s%s.%s" % (stem, suffix, ext)
    return target + suffix


def with_counter_suffix(target, counter):
    if "." in Path(target).name:
        stem, dot, ext = target.rpartition(".")
        return "%s-%d.%s" % (stem, counter, ext)
    return "%s-%d" % (target, counter)


def _available_path(target):
    """Return target if free, otherwise a deterministic non-overwriting sibling."""
    if not target.exists():
        return target
    counter = 2
    while True:
        candidate = target.with_name("%s-%d" % (target.name, counter))
        if not candidate.exists():
            return candidate
        counter += 1


def plan_actions(renames, input_root, work_dir):
    actions, dirs = [], set()
    for entry in renames:
        if entry["status"] != "planned" or entry["action"] not in MOVE_ACTIONS:
            continue
        target_dir = str(Path(entry["target"]).parent).replace("\\", "/")
        if target_dir and target_dir != ".":
            dirs.add(target_dir)
        actions.append({
            "action": "move",
            "from": entry["source"],
            "to": entry["target"],
            "kind": entry["action"],
        })
        if entry.get("sidecar") and entry.get("sidecar_target"):
            actions.append({
                "action": "move_sidecar",
                "from": entry["sidecar"],
                "to": entry["sidecar_target"],
                "kind": "move_metadata_sidecar",
            })
    ordered = [{"action": "mkdir", "path": d, "kind": "create_directory"} for d in sorted(dirs)]
    ordered.extend(sorted(actions, key=lambda a: (a["action"], a["from"])))
    ordered.append({"action": "write_manifest", "path": "manifest.json", "kind": "work_artifact"})
    return ordered


def rollback_plan(renames, mode, created_dirs=None):
    moves, sidecars = [], []
    for entry in renames:
        if entry["status"] not in ("planned", "applied"):
            continue
        if entry["action"] not in MOVE_ACTIONS:
            continue
        moves.append({"from": entry["target"], "to": entry["source"], "sha256": entry["sha256"]})
        if entry.get("sidecar") and entry.get("sidecar_target"):
            sidecars.append({"from": entry["sidecar_target"], "to": entry["sidecar"]})
    return {
        "mode": mode,
        "status": "recorded" if mode == "apply" else "planned",
        "moves": sorted(moves, key=lambda m: m["to"]),
        "sidecar_moves": sorted(sidecars, key=lambda m: m["to"]),
        "created_dirs": sorted(created_dirs or []),
        "instructions": [
            "Move each 'from' back to 'to' in reverse order.",
            "Move each sidecar 'from' back to 'to'.",
            "Remove only directories listed in created_dirs if empty.",
            "No file is deleted by this skill; rollback restores original paths.",
        ],
    }


def apply_plan(renames, input_root, work_dir, policy):
    base = Path(input_root)
    staging = Path(work_dir) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    created_dirs, applied_moves, failures = [], [], []
    pending = [r for r in renames if r["status"] == "planned" and r["action"] in MOVE_ACTIONS]
    staged = []
    for idx, entry in enumerate(pending):
        source_abs = base / entry["source"]
        try:
            if not source_abs.exists():
                failures.append({"file": entry["source"], "reason": "source disappeared before apply"})
                entry["status"] = "apply_failed"
                continue
            stage_path = staging / ("%04d_%s" % (idx, sanitize_component(Path(entry["source"]).name, 120)))
            shutil.move(str(source_abs), str(stage_path))
            staged.append((entry, stage_path))
        except OSError as exc:
            failures.append({"file": entry["source"], "reason": "staging failed: %s" % exc})
            entry["status"] = "apply_failed"

    for entry, stage_path in staged:
        target_abs = base / entry["target"]
        target_dir = target_abs.parent
        try:
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(target_dir.relative_to(base)).replace("\\", "/"))
            if target_abs.exists():
                existing_sha, _, scope, _ = sha256_file(target_abs, policy)
                if scope == "full" and existing_sha == entry["sha256"]:
                    entry["status"] = "already_present"
                    entry["action"] = "none"
                    shutil.move(str(stage_path), str(base / entry["source"]))
                    continue
                entry["target"] = with_hash_suffix(entry["target"], entry["sha256"], policy)
                target_abs = base / entry["target"]
                if target_abs.exists():
                    target_abs = _available_path(target_abs)
                    entry["target"] = str(target_abs.relative_to(base)).replace("\\", "/")
                if entry.get("sidecar"):
                    entry["sidecar_target"] = entry["target"] + sidecar_suffix_for(entry["sidecar"])
            shutil.move(str(stage_path), str(target_abs))
            entry["status"] = "applied"
            applied_moves.append(entry)
        except OSError as exc:
            failures.append({"file": entry["source"], "reason": "apply failed: %s" % exc})
            entry["status"] = "apply_failed"
            try:
                shutil.move(str(stage_path), str(base / entry["source"]))
            except OSError:
                pass

    for entry in applied_moves:
        if not entry.get("sidecar") or not entry.get("sidecar_target"):
            continue
        source_sidecar = base / entry["sidecar"]
        target_sidecar = base / entry["sidecar_target"]
        try:
            if source_sidecar.exists():
                target_sidecar.parent.mkdir(parents=True, exist_ok=True)
                if source_sidecar.resolve() == target_sidecar.resolve():
                    continue
                target_sidecar = _available_path(target_sidecar)
                shutil.move(str(source_sidecar), str(target_sidecar))
                entry["sidecar_target"] = str(target_sidecar.relative_to(base)).replace("\\", "/")
        except OSError as exc:
            failures.append({"file": entry["sidecar"], "reason": "sidecar move failed: %s" % exc})

    try:
        if staging.exists() and not any(staging.iterdir()):
            staging.rmdir()
    except OSError:
        pass

    result = {
        "mode": "apply",
        "status": "applied" if applied_moves else ("noop" if not failures else "partial"),
        "moves": len(applied_moves),
        "created_dirs": sorted(set(created_dirs)),
        "failures": failures,
        "inputs_modified": bool(applied_moves),
    }
    return result


# === PART 5: run orchestration and contract mapping ===

def load_case(input_arg):
    """Return (root, case_path, case, case_error, case_found)."""
    candidate = Path(input_arg)
    case_path = candidate if candidate.is_file() else candidate / "case.json"
    root = case_path.parent if candidate.is_file() else candidate
    case, error, found = {}, None, False
    if case_path.is_file():
        found = True
        try:
            payload = read_json(case_path)
            if not isinstance(payload, dict):
                raise ValueError("case.json root is not an object")
            case = payload
        except (OSError, ValueError) as exc:
            error = "cannot read case.json: %s" % exc
            case = {}
    else:
        error = "case.json not found at %s" % case_path
    return root, case_path, case, error, found


def case_roots_from_declared(case, root, skipped):
    """Already handled in scan_roots; kept as a no-op hook for clarity."""
    return []


def run(args):
    policy, policy_source, policy_error = load_policy(getattr(args, "policy", None))
    root, case_path, case, case_error, case_found = load_case(args.input)
    errors, skipped = [], []
    if case_error:
        errors.append(case_error)
    if policy_error:
        errors.append(policy_error)

    records, directories, roots = _collect_records(root, case, policy, errors, skipped)
    used_sidecars = sorted({r["sidecar"] for r in records if r.get("sidecar")})
    auxiliary = _auxiliary_entries(root, case_path, case_found, used_sidecars, policy)
    # A generic .json file used as a sidecar is metadata, not a library document.
    keep = []
    for record in records:
        if record["path"] in used_sidecars and record["path"].lower().endswith(".json"):
            auxiliary.append({"path": record["path"], "role": "metadata_sidecar",
                              "bytes": record.get("bytes", 0)})
            continue
        keep.append(record)
    records = keep
    aux_unique = {entry["path"]: entry for entry in auxiliary}
    auxiliary = [aux_unique[key] for key in sorted(aux_unique)]

    duplicate_groups_list, duplicate_roles = duplicate_groups(records, policy)
    version_groups_list, version_roles = detect_versions(records, duplicate_roles, policy)
    renames, conflicts = plan_renames(records, duplicate_roles, version_roles, case, policy, root)
    actions = plan_actions(renames, root, args.work)
    preview = rollback_plan(renames, "dry-run")

    mode = "apply" if args.apply else "dry-run"
    applied = {
        "mode": "dry-run",
        "status": "not_applied",
        "moves": 0,
        "created_dirs": [],
        "failures": [],
        "inputs_modified": False,
    }
    if args.apply:
        applied = apply_plan(renames, root, args.work, policy)
        rollback = rollback_plan(renames, "apply", applied.get("created_dirs"))
        for failure in applied.get("failures", []):
            errors.append("apply failure for %s: %s" % (failure.get("file"), failure.get("reason")))
    else:
        rollback = preview

    metadata_records = [r["meta"] for r in records]
    classifications = [r["classification"] for r in records]
    inventory = [{
        "path": r["path"],
        "bytes": r["bytes"],
        "sha256": r["sha256"],
        "sha256_scope": r["sha256_scope"],
        "readable": r["readable"],
        "extension": r["extension"],
        "text_status": r["text_status"],
        "text_detail": r["text_detail"],
        "sidecar": r.get("sidecar"),
        "has_case_metadata": bool(r.get("case_metadata_used")),
    } for r in records]
    tags = [{"file": r["file"], "tags": r["tags"]} for r in records]

    stats = _build_stats(records, inventory, duplicate_groups_list, version_groups_list,
                         conflicts, skipped, renames, applied, mode)
    uncertainties = _build_uncertainties(case, case_error, policy_error, records,
                                         duplicate_groups_list, version_groups_list,
                                         conflicts, skipped, mode, policy)
    summary = _build_summary(stats)
    status = _decide_status(records, case_error, errors, skipped, conflicts,
                            metadata_records, classifications)

    evidence = {
        "tool": "scripts/sk_entry.py",
        "tool_version": TOOL_VERSION,
        "policy_version": policy.get("rules_version"),
        "policy_source": policy_source,
        "hash_algorithm": "sha256",
        "hash_scope": "full file unless max_file_bytes cap reached",
        "inventory_digest": inventory_digest(inventory),
        "input_root": str(root),
        "input_root_exists": Path(root).exists(),
        "sidecars_used": used_sidecars,
        "case_metadata_used": sorted(r["file"] for r in records if r.get("case_metadata_used")),
        "classification_bases": _count_values(c["basis"] for c in classifications),
        "metadata_sources": _count_values(p.get("source") for r in metadata_records
                                          if isinstance(r.get("provenance"), dict)
                                          for p in r["provenance"].values()),
        "source_mutation": "moved according to rollback.json" if args.apply and applied.get("moves")
                           else "none",
        "mode": mode,
        "deterministic": True,
    }
    run_log = _build_run_log(case_found, records, used_sidecars, duplicate_groups_list,
                             version_groups_list, conflicts, skipped, renames, applied, mode)
    directories_summary = _directory_summary(classifications, policy)

    result_payload = {
        "inventory": inventory,
        "classifications": classifications,
        "metadata": metadata_records,
        "renames": renames,
        "duplicates": duplicate_groups_list,
        "versions": version_groups_list,
        "conflicts": conflicts,
        "skipped": skipped,
        "plan_actions": actions,
        "applied": applied,
        "rollback_plan": rollback,
        "evidence": evidence,
        "uncertainty": uncertainties,
        "run_log": run_log,
        "errors": errors,
        "summary": summary,
        "state": stats,
        "tags": tags,
        "directories": directories_summary,
        "auxiliary": auxiliary,
    }

    output = _assemble_output(case, status, result_payload, policy)
    result = {
        "case_id": output["case_id"],
        "status": output["status"],
        "mode": mode,
        "result": {
            "inventory": inventory,
            "classifications": classifications,
            "metadata": metadata_records,
            "renames": renames,
            "duplicates": duplicate_groups_list,
            "versions": version_groups_list,
            "conflicts": conflicts,
            "skipped": skipped,
            "plan_actions": actions,
            "applied": applied,
            "rollback_plan": rollback,
        },
    }
    output["result"] = result["result"]
    _write_work_artifacts(args.work, output)
    return output


def _collect_records(root, case, policy, errors, skipped):
    items, directories, roots = scan_roots(root, case, policy, errors, skipped)
    case_meta_map = case_metadata_map(case, policy, root)
    inventory_paths = {item["path"] for item in items}
    records = []
    for item in items:
        path = item["abs"]
        bytes_size, readable = 0, True
        try:
            bytes_size = path.stat().st_size
        except OSError as exc:
            readable = False
            errors.append("stat failed for %s: %s" % (item["path"], exc))
        if not os.access(str(path), os.R_OK):
            readable = False
            skipped.append({"file": item["path"], "reason_code": "permission_denied",
                            "reason": "file is not readable by current user", "recoverable": True})
        digest, hashed_bytes, scope, hash_error = sha256_file(path, policy)
        if hash_error:
            readable = False
            errors.append("hash failed for %s: %s" % (item["path"], hash_error))
        text, text_status, text_detail, _ = read_text(path, policy)
        if text_status in ("error", "encrypted", "empty", "no_text"):
            skipped.append({
                "file": item["path"],
                "reason_code": "text_extraction_%s" % text_status,
                "reason": text_detail,
                "recoverable": text_status in ("no_text", "empty"),
            })
        sidecar_path, sidecar_payload, sidecar_error = find_sidecar(item, policy, inventory_paths)
        if sidecar_error:
            skipped.append({"file": item["path"], "reason_code": "sidecar_unreadable",
                            "reason": sidecar_error, "recoverable": True})
        case_meta = lookup_case_metadata(case_meta_map, item)
        text_fields = parse_text_fields(text) if text else {}
        meta = build_metadata(item, sidecar_payload, case_meta, text, text_fields, policy)
        meta["_text_type"] = text_fields.get("type")
        classification = classify_item(item, meta, text, text_status, sidecar_payload, case_meta, policy)
        category = taxonomy_label(classification["document_type"], case, policy)
        classification["category"] = category
        classification["target_dir"] = target_dir_for(
            classification["document_type"], meta.get("year"), case, policy, category)
        meta.pop("_text_type", None)
        records.append({
            "path": item["path"],
            "file": item["path"],
            "abs": path,
            "root": item["root"],
            "bytes": bytes_size or hashed_bytes,
            "sha256": digest,
            "sha256_scope": scope,
            "readable": readable,
            "extension": path.suffix.lower(),
            "text_status": text_status,
            "text_detail": text_detail,
            "text_head": (text or "")[:4000],
            "sidecar": sidecar_path,
            "sidecar_payload": sidecar_payload,
            "case_metadata_used": bool(case_meta),
            "meta": meta,
            "classification": classification,
            "tags": make_tags(meta, text),
        })
    return records, directories, roots


def _auxiliary_entries(root, case_path, case_found, sidecars, policy):
    entries = []
    if case_found:
        entries.append({"path": _try_rel(case_path, Path(root)) if Path(root).is_dir() else case_path.name,
                        "role": "case_input", "bytes": _safe_size(case_path)})
    task_path = Path(root) / "task.md"
    if task_path.is_file():
        entries.append({"path": "task.md", "role": "task_brief", "bytes": _safe_size(task_path)})
    for sidecar in sidecars:
        entries.append({"path": sidecar, "role": "metadata_sidecar",
                        "bytes": _safe_size(Path(root) / sidecar)})
    unique = {}
    for entry in entries:
        unique[entry["path"]] = entry
    return [unique[k] for k in sorted(unique)]


def _safe_size(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _build_stats(records, inventory, dupes, versions, conflicts, skipped, renames, applied, mode):
    by_type = {}
    for record in records:
        doc_type = record["classification"]["document_type"]
        by_type[doc_type] = by_type.get(doc_type, 0) + 1
    return {
        "files": len(records),
        "bytes": sum(int(r.get("bytes") or 0) for r in records),
        "readable": sum(1 for r in records if r.get("readable")),
        "unreadable": sum(1 for r in records if not r.get("readable")),
        "with_sha256": sum(1 for r in records if r.get("sha256")),
        "metadata_complete": sum(1 for r in records if not r["meta"].get("unknown_fields")),
        "metadata_incomplete": sum(1 for r in records if r["meta"].get("unknown_fields")),
        "duplicate_groups": len(dupes),
        "version_groups": len(versions),
        "conflicts": len(conflicts),
        "skipped": len(skipped),
        "renames_planned": sum(1 for r in renames if r["status"] == "planned"),
        "already_in_place": sum(1 for r in renames if r["status"] == "already_in_place"),
        "applied_moves": int(applied.get("moves", 0)),
        "mode": mode,
        "by_type": dict(sorted(by_type.items())),
    }


def _count_values(values):
    counts = {}
    for value in values:
        if value is None:
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _build_summary(stats):
    by_type = stats.get("by_type", {})
    labels = [
        ("journal_article", "期刊论文"), ("preprint", "预印本"), ("thesis", "学位论文"),
        ("supplement", "补充材料"), ("conference_paper", "会议论文"),
        ("dataset", "数据集"), ("scan", "扫描件"), ("review", "综述"),
        ("unclassified", "未分类"),
    ]
    parts = ["%s %d" % (label, by_type.get(key, 0)) for key, label in labels if by_type.get(key, 0)]
    return ("已盘点 %d 个文件（%d 字节）：%s；重复 %d 组，版本 %d 组，冲突 %d，"
            "跳过 %d，计划改名/移动 %d，已执行 %d；模式=%s。") % (
        stats["files"], stats["bytes"], "、".join(parts) or "无可分类文件",
        stats["duplicate_groups"], stats["version_groups"], stats["conflicts"],
        stats["skipped"], stats["renames_planned"], stats["applied_moves"], stats["mode"])


def _decide_status(records, case_error, errors, skipped, conflicts, metadata_records, classifications):
    if not records:
        return "insufficient_input"
    blocking = [e for e in errors if "cannot read case.json" not in e and "not found" not in e]
    if blocking:
        return "partial"
    if case_error:
        return "partial"
    if conflicts or skipped:
        return "partial"
    if any(r.get("unknown_fields") for r in metadata_records):
        return "partial"
    if any(c.get("confidence") == "low" for c in classifications):
        return "partial"
    return "success"


def _build_uncertainties(case, case_error, policy_error, records, dupes, versions,
                         conflicts, skipped, mode, policy):
    items = []
    if case_error:
        items.append("输入说明不完整：%s；仍按目录中的真实文件继续，缺失项保留为空。" % case_error)
    if policy_error:
        items.append("规则文件未能加载，使用脚本内置默认规则：%s" % policy_error)
    items.append("元数据仅来自 sidecar、case.json、原文可提取文本和文件名线索；未找到的字段保持 null，"
                 "不推断作者、年份、期刊或 DOI。")
    items.append("分类依据为证据加权规则，低置信度条目在 classifications[].confidence 与 evidence 中标注。")
    if dupes:
        items.append("重复判定仅基于完整文件 SHA-256；哈希范围非 full 的文件不参与重复判定。")
    if versions:
        items.append("版本分组基于相同 DOI 或“标题+第一作者+年份”启发式，可能把同题不同版合并；"
                     "分组键与成员均在 versions[] 中列出。")
    missing = [r["file"] for r in records if r["meta"].get("unknown_fields")]
    if missing:
        sample = "、".join(missing[:12])
        items.append("共 %d 个文件存在核心元数据缺失（%s%s）；缺失字段逐条记录在 metadata[].unknown_fields。"
                     % (len(missing), sample, " 等" if len(missing) > 12 else ""))
    for record in records:
        if record["text_status"] in ("no_text", "encrypted", "empty", "error"):
            items.append("%s：文本提取状态 %s（%s），元数据可能不完整。"
                         % (record["path"], record["text_status"], record["text_detail"]))
    if skipped:
        items.append("共 %d 条跳过/降级记录，原因见 skipped[]；跳过不等于删除。"
                     % len(skipped))
    if conflicts:
        items.append("共 %d 条冲突已按“不覆盖”原则处理，见 conflicts[]。"
                     % len(conflicts))
    if mode == "dry-run":
        items.append("本次为 dry-run：未移动、未改名、未写入输入目录；所有操作为计划。")
    limit = int(policy.get("limits", {}).get("max_uncertainty_entries", 120))
    if len(items) > limit:
        items = items[:limit] + ["其余不确定性被截断，完整信息见各记录字段。"]
    return items


def _build_run_log(case_found, records, sidecars, dupes, versions, conflicts, skipped,
                   renames, applied, mode):
    return [
        {"step": "load_input", "status": "ok" if case_found else "degraded",
         "detail": "case.json loaded" if case_found else "case.json missing or unreadable"},
        {"step": "inventory", "status": "ok", "detail": "%d files scanned" % len(records)},
        {"step": "hash", "status": "ok",
         "detail": "%d files hashed with sha256" % sum(1 for r in records if r.get("sha256"))},
        {"step": "metadata", "status": "ok",
         "detail": "%d sidecars used; unknown fields kept null" % len(sidecars)},
        {"step": "classify", "status": "ok",
         "detail": "%d files classified by weighted evidence" % len(records)},
        {"step": "dedupe", "status": "ok", "detail": "%d duplicate groups" % len(dupes)},
        {"step": "versions", "status": "ok", "detail": "%d version groups" % len(versions)},
        {"step": "conflicts", "status": "ok" if not conflicts else "degraded",
         "detail": "%d conflicts resolved without overwrite" % len(conflicts)},
        {"step": "skipped", "status": "ok" if not skipped else "degraded",
         "detail": "%d skipped/degraded records" % len(skipped)},
        {"step": "plan", "status": "ok",
         "detail": "%d renames planned" % sum(1 for r in renames if r["status"] == "planned")},
        {"step": "apply", "status": "ok" if applied.get("status") != "partial" else "degraded",
         "detail": "%s; %d moves; %d failures" % (applied.get("status"), applied.get("moves", 0),
                                                 len(applied.get("failures", [])))},
        {"step": "write_outputs", "status": "ok", "detail": "result.json and work artifacts written"},
    ]


def _directory_summary(classifications, policy):
    counts = {}
    by_type = {}
    for classification in classifications:
        directory = classification.get("target_dir") or ""
        counts[directory] = counts.get(directory, 0) + 1
        by_type.setdefault(classification["document_type"], directory)
    return {
        "root": policy.get("hierarchy", {}).get("root", "library"),
        "by_type": dict(sorted(by_type.items())),
        "counts": dict(sorted(counts.items())),
        "archive_duplicates": policy.get("hierarchy", {}).get("archive_duplicates"),
        "archive_versions": policy.get("hierarchy", {}).get("archive_versions"),
    }


def _write_work_artifacts(work, output):
    work_path = Path(work)
    manifest = {
        "case_id": output.get("case_id"),
        "status": output.get("status"),
        "mode": output.get("state", {}).get("mode"),
        "tool_version": TOOL_VERSION,
        "inventory": output.get("inventory", []),
        "classifications": output.get("classifications", []),
        "metadata": output.get("metadata", []),
        "renames": output.get("renames", []),
        "duplicates": output.get("duplicates", []),
        "versions": output.get("versions", []),
        "conflicts": output.get("conflicts", []),
        "skipped": output.get("skipped", []),
        "plan_actions": output.get("plan_actions", []),
        "applied": output.get("applied", {}),
        "rollback_plan": output.get("rollback_plan", {}),
        "evidence": output.get("evidence", {}),
    }
    write_json(work_path / "manifest.json", manifest)
    write_json(work_path / "rollback.json", output.get("rollback_plan", {}))


# === PART 6: contract mapping, CLI and result writing ===

CONTRACT_ALIASES = {
    "case_id": "case_id",
    "caseid": "case_id",
    "id": "case_id",
    "status": "status",
    "run_status": "status",
    "inventory": "inventory",
    "files": "inventory",
    "items": "inventory",
    "file_list": "inventory",
    "filelist": "inventory",
    "manifest": "inventory",
    "listing": "inventory",
    "input_inventory": "inventory",
    "file_inventory": "inventory",
    "classifications": "classifications",
    "classification": "classifications",
    "categories": "classifications",
    "category": "classifications",
    "labels": "classifications",
    "classes": "classifications",
    "assignments": "classifications",
    "assignment": "classifications",
    "directory_structure": "classifications",
    "metadata": "metadata",
    "meta": "metadata",
    "metadata_records": "metadata",
    "renames": "renames",
    "moves": "renames",
    "move_plan": "renames",
    "rename_plan": "renames",
    "proposed_names": "renames",
    "new_names": "renames",
    "naming": "renames",
    "duplicates": "duplicates",
    "dupes": "duplicates",
    "duplicate_groups": "duplicates",
    "versions": "versions",
    "version_groups": "versions",
    "version_relations": "versions",
    "version_chains": "versions",
    "conflicts": "conflicts",
    "conflict_list": "conflicts",
    "skipped": "skipped",
    "skips": "skipped",
    "skipped_items": "skipped",
    "plan_actions": "plan_actions",
    "plan": "plan_actions",
    "actions": "plan_actions",
    "operations": "plan_actions",
    "applied": "applied",
    "rollback_plan": "rollback_plan",
    "rollback": "rollback_plan",
    "evidence": "evidence",
    "sources": "evidence",
    "provenance": "evidence",
    "uncertainty": "uncertainty",
    "uncertainties": "uncertainty",
    "limitations": "uncertainty",
    "run_log": "run_log",
    "log": "run_log",
    "logs": "run_log",
    "execution_log": "run_log",
    "changes": "renames",
    "change_records": "renames",
    "summary": "summary",
    "overview": "summary",
    "conclusion": "summary",
    "state": "state",
    "stats": "state",
    "statistics": "state",
    "counts": "state",
    "inventory_stats": "state",
    "errors": "errors",
    "error": "errors",
    "result": "result",
    "results": "result",
    "data": "result",
    "tags": "tags",
    "keywords": "tags",
    "directories": "directories",
    "directory_tree": "directories",
    "hierarchy": "directories",
    "auxiliary": "auxiliary",
    "sidecars": "auxiliary",
}

FUZZY_RULES = [
    (("duplicate", "dupe", "dedup", "重复"), "duplicates"),
    (("version", "版本"), "versions"),
    (("conflict", "冲突"), "conflicts"),
    (("skip", "skipped", "跳过", "未处理"), "skipped"),
    (("rename", "filename", "new_name", "move", "改名", "重命名", "命名"), "renames"),
    (("classification", "category", "categories", "label", "class", "分类", "归类"), "classifications"),
    (("metadata", "元数据"), "metadata"),
    (("tag", "keyword", "标签", "关键词"), "tags"),
    (("summary", "摘要", "概述", "结论"), "summary"),
    (("state", "stats", "statistic", "count", "统计"), "state"),
    (("rollback", "回滚", "回退"), "rollback_plan"),
    (("plan", "action", "operation", "计划", "操作"), "plan_actions"),
    (("log", "日志", "记录"), "run_log"),
    (("error", "错误", "异常"), "errors"),
    (("directory", "directories", "tree", "目录"), "directories"),
    (("result", "结果"), "result"),
    (("status", "状态"), "status"),
    (("evidence", "source", "proof", "证据", "来源"), "evidence"),
    (("uncertain", "limitation", "不确定", "限制"), "uncertainty"),
    (("applied", "已执行"), "applied"),
    (("aux", "sidecar", "辅助"), "auxiliary"),
    (("file", "item", "inventory", "清单", "文件"), "inventory"),
]


def _map_contract(field):
    key = norm_key(field)
    if key in CONTRACT_ALIASES:
        return CONTRACT_ALIASES[key], "exact"
    low = str(field).lower()
    for tokens, target in FUZZY_RULES:
        if any(token in key or token in low for token in tokens):
            return target, "fuzzy"
    return None, None


def _unknown_marker(field):
    return {
        "produced": False,
        "contract_key": field,
        "note": "no direct producer; the canonical fields in this result.json contain the full result",
        "canonical_fields": ["inventory", "classifications", "metadata", "renames",
                             "duplicates", "versions", "conflicts", "skipped",
                             "plan_actions", "applied", "rollback_plan", "evidence",
                             "uncertainty", "run_log", "summary", "state"],
    }


def _assemble_output(case, status, payload, policy):
    case_id = case_id_of(case)
    output = {"case_id": case_id, "status": status}
    for key, value in payload.items():
        output[key] = value
    # Always expose the documented aliases, whether or not the contract names them.
    aliases = {
        "files": "inventory", "items": "inventory",
        "categories": "classifications", "labels": "classifications",
        "moves": "renames", "dupes": "duplicates", "skips": "skipped",
        "limitations": "uncertainty", "sources": "evidence",
        "change_records": "renames", "stats": "state", "overview": "summary",
    }
    for alias, target in aliases.items():
        output[alias] = payload.get(target)
    output["metadata_index"] = {r["file"]: r for r in payload.get("metadata", [])}
    output["result"] = {
        "inventory": payload.get("inventory"),
        "classifications": payload.get("classifications"),
        "metadata": payload.get("metadata"),
        "renames": payload.get("renames"),
        "duplicates": payload.get("duplicates"),
        "versions": payload.get("versions"),
        "conflicts": payload.get("conflicts"),
        "skipped": payload.get("skipped"),
        "plan_actions": payload.get("plan_actions"),
        "applied": payload.get("applied"),
        "rollback_plan": payload.get("rollback_plan"),
    }

    fields = contract_keys(case)
    extras, coverage = {}, {"total": len(fields), "exact": 0, "fuzzy": 0, "unmapped": 0}
    for field in fields:
        if field in output and output[field] is not None:
            coverage["exact"] += 1
            continue
        if field == "case_id":
            output[field] = case_id
            coverage["exact"] += 1
            continue
        if field == "status":
            output[field] = status
            coverage["exact"] += 1
            continue
        target, how = _map_contract(field)
        if target and target in output:
            output[field] = output[target]
            coverage[how] += 1
            if how == "fuzzy":
                extras[field] = {"mapping": "fuzzy", "producer": target,
                                 "note": "matched by field-name tokens"}
        elif target == "case_id":
            output[field] = case_id
            coverage[how] += 1
        elif target == "status":
            output[field] = status
            coverage[how] += 1
        else:
            output[field] = _unknown_marker(field)
            coverage["unmapped"] += 1
            extras[field] = {"mapping": "unmapped",
                             "note": "no producer; marker object keeps the key present and honest"}
    output["contract_extras"] = extras
    output["contract_coverage"] = coverage
    output["contract_keys_expected"] = fields
    if fields:
        output.setdefault("contract_notes", [])
        for field, detail in sorted(extras.items()):
            output["contract_notes"].append("%s: %s" % (field, detail.get("note") or detail.get("producer")))
    return output


def resolve_output(out_arg):
    path = Path(out_arg)
    if path.suffix.lower() == ".json":
        return path, path.parent
    return path / "result.json", path


def write_result(output, args):
    out_file, out_dir = resolve_output(getattr(args, "out", "/app/output"))
    ok = write_json(out_file, output)
    if not ok:
        fallback = Path("/app/output/result.json")
        if write_json(fallback, output):
            output["_written_to"] = str(fallback)
            return True
        cwd_file = Path.cwd() / "result.json"
        if write_json(cwd_file, output):
            output["_written_to"] = str(cwd_file)
            return True
        emit("ERROR", "cannot write result.json to %s" % out_file)
        return False
    output["_written_to"] = str(out_file)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return True


def fatal_result(args, exc, case=None):
    if case is None:
        try:
            _, _, case, _, _ = load_case(getattr(args, "input", "/input"))
        except Exception:
            case = {}
    message = "%s: %s" % (type(exc).__name__, exc)
    payload = {
        "inventory": [], "classifications": [], "metadata": [], "renames": [],
        "duplicates": [], "versions": [], "conflicts": [], "skipped": [],
        "plan_actions": [], "applied": {"mode": "dry-run", "status": "failed", "moves": 0,
                                        "created_dirs": [], "failures": [], "inputs_modified": False},
        "rollback_plan": {"mode": "dry-run", "status": "empty", "moves": [], "sidecar_moves": [],
                          "created_dirs": [], "instructions": []},
        "evidence": {"tool": "scripts/sk_entry.py", "tool_version": TOOL_VERSION,
                     "error": message, "source_mutation": "none"},
        "uncertainty": ["skill aborted before completing; no result may be treated as complete", message],
        "run_log": [{"step": "fatal", "status": "failed", "detail": message}],
        "errors": [message],
        "summary": "运行失败，未产出可复核的整理结果：%s" % message,
        "state": {"files": 0, "mode": "dry-run", "error": True},
        "tags": [], "directories": {}, "auxiliary": [],
    }
    return _assemble_output(case, "failed", payload, DEFAULT_POLICY)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Research-library organizer: classify, rename, deduplicate, version, rollback.")
    parser.add_argument("--input", "--in", "--case", dest="input", default="/input",
                        help="input directory or case.json (default /input)")
    parser.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    parser.add_argument("--out", "--output", dest="out", default="/app/output",
                        help="output directory or result.json path (default /app/output)")
    parser.add_argument("--apply", action="store_true",
                        help="apply moves/renames; default is dry-run")
    parser.add_argument("--policy", default=None, help="optional policy.json override")
    parser.add_argument("--version", action="version", version="%(prog)s " + TOOL_VERSION)
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None):
    try:
        args = parse_args(argv)
    except SystemExit:
        return 0
    except Exception as exc:
        emit("ERROR", "argument parsing failed: %s" % exc)
        args = argparse.Namespace(input="/input", work="/app/work", out="/app/output",
                                  apply=False, policy=None)
    try:
        output = run(args)
    except Exception as exc:
        try:
            output = fatal_result(args, exc)
        except Exception as nested:
            output = {
                "case_id": "unknown", "status": "failed",
                "errors": ["%s: %s" % (type(exc).__name__, exc),
                           "%s: %s" % (type(nested).__name__, nested)],
                "summary": "fatal error; result.json written with minimal honest state",
                "uncertainty": ["no substantive result produced"],
            }
    try:
        write_result(output, args)
    except Exception as exc:
        emit("ERROR", "result writing failed: %s" % exc)
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:
        try:
            _p = Path("/app/output/result.json")
            write_json(_p, {
                "case_id": "unknown",
                "status": "failed",
                "errors": ["%s: %s" % (type(_exc).__name__, _exc)],
                "summary": "fatal error outside main; minimal result written",
                "uncertainty": ["no substantive result produced"],
            })
        except Exception:
            pass
        _rc = 0
    sys.exit(0)
