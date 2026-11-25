# Quickstart

## 1. 默认复制落库

```bash
cd /skills/weekly-skill
python scripts/sk_entry.py \
  --input examples/demo-case \
  --work /app/work \
  --out /app/output
```

退出码应为 `0`，生成 `/app/output/result.json`、`/app/output/rollback.json` 与 `library/` 下的输出副本，同时写 `/app/work/manifest.json`。默认不修改 `examples/demo-case`；只需计划时加 `--dry-run`。

## 2. 检查结果

```bash
python - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
print('case_id:', r['case_id'])
print('status :', r['status'])
print('mode   :', r['state']['mode'])
print('files  :', len(r['inventory']))
print('types  :', r['state']['by_type'])
print('dupes  :', len(r['duplicates']), 'versions:', len(r['versions']))
print('conflicts:', len(r['conflicts']), 'skipped:', len(r['skipped']))
print('missing contract keys:', [k for k in r['contract_keys_expected'] if k not in r])
for c in r['classifications']:
    print(c['file'], '->', c['document_type'], c['confidence'], c['basis'])
for m in r['metadata']:
    print(m['file'], 'unknown=', m['unknown_fields'], 'provenance=', list(m['provenance']))
PY
```

demo 的预期特征：

- 7 个资料文件；`journal_article 2`、`preprint 1`、`thesis 1`、`supplement 2`、`scan 1`；
- 1 组重复（`journal-article-2023.pdf` 与 `copy-of-journal-article-2023.pdf`，SHA-256 相同）；
- 1 组版本（`smith-2020-graphene-preprint.pdf` 与 `smith-2020-graphene-formal.pdf`，`preprint_vs_formal`）；
- 1 条跳过：扫描件无文本层；
- 1 条冲突（`copy-of-journal-article-2023.pdf` 与 `journal-article-2023.pdf` 同为重复下载，后者归档为副本，不删除）；7 条输出副本计划；
- `status=partial`（存在未知元数据与扫描件，如实报告，不是失败）；
- `metadata` 中 thesis 的 `journal`、`doi` 为 `null`；scan 的 `authors`、`journal`、`doi` 为 `null`。

检查契约键：

```bash
python - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r['case_id'] and r['status']
assert not [k for k in r['contract_keys_expected'] if k not in r]
for alias in ('files', 'items', 'categories', 'labels', 'moves', 'dupes', 'skips', 'summary', 'state'):
    assert alias in r, alias
print('contract OK', r['contract_coverage'])
PY
```

## 3. 默认复制与重跑

直接对 `examples/demo-case` 运行即可，源文件不会被移动：

```bash
python scripts/sk_entry.py \
  --input examples/demo-case \
  --work /tmp/demo-work \
  --out /tmp/demo-out
```

预期：

- `applied.status = "applied"`，`moves = 7`，`inputs_modified = false`，`source_mutation = "none"`；
- `applied.library_root = "library"`，7 个文件位于 `/tmp/demo-out/library/<type>/<year>/`；
- `moves[].target` 为输出侧相对路径且带 `target_sha256`；
- `inbox/*.pdf` 原样保留，哈希不变；`rollback.json` 的 `created_files` 指向输出副本。

再次执行同一命令应为幂等：

```bash
python scripts/sk_entry.py --input examples/demo-case --work /tmp/demo-work --out /tmp/demo-out
# 输出文件数不增长，result.json 与首次逐字节一致
```

## 4. 回滚

删除 `rollback.json` 的 `created_files` 中列出的输出副本，再移除为空的 `created_dirs` 即可：

```bash
python - <<'PY'
import json
from pathlib import Path
out = Path('/tmp/demo-out')
rb = json.load(open(out / 'rollback.json', encoding='utf-8'))
for rel in rb['created_files']:
    (out / rel).unlink(missing_ok=True)
for rel in sorted(rb['created_dirs'], key=len, reverse=True):
    try:
        (out / rel).rmdir()
    except OSError:
        pass
print('rolled back')
PY
```

回滚只影响输出目录；输入 demo 文件从未被修改。

## 5. 接入真实 case

```bash
python /skills/weekly-skill/scripts/sk_entry.py \
  --input /input \
  --work /app/work \
  --out /app/output
```

- `/input/case.json` 可提供 `case_id`、`naming_template`、`taxonomy`、`output_contract`、`metadata`。
- 所有 `output_contract` 键都会出现在 `result.json`；未知键在 `contract_extras` 标记。
- 任何空目录、损坏输入或内部异常都返回退出码 0，并在 `status`/`errors` 中如实说明。

## 6. 文件上限

本包只用 Python 标准库，不联网，确定性输出；提交包自身 ≤50 个文件、单文件 ≤500 KB、总计 ≤2 MB，无符号链接、无 `__pycache__`。
