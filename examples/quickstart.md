# Quickstart

## 1. 只读 dry-run

```bash
cd /skills/weekly-skill
python scripts/sk_entry.py \
  --input examples/demo-case \
  --work /app/work \
  --out /app/output
```

退出码应为 `0`，生成 `/app/output/result.json` 与 `/app/work/manifest.json`、`/app/work/rollback.json`。dry-run 不会改动 `examples/demo-case`。

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
- 0 冲突；7 条计划改名/移动；
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

## 3. 在副本上演示 apply

不要直接对 `examples/demo-case` 做 apply；先复制：

```bash
cp -r examples/demo-case /tmp/demo-apply
python scripts/sk_entry.py \
  --input /tmp/demo-apply \
  --work /tmp/demo-work \
  --out /tmp/demo-out \
  --apply
```

预期：

- `applied.status = "applied"`，`moves = 7`；
- `library/journal_article/2023/...pdf` 等目标存在；
- 原 `inbox/*.pdf` 不再存在（已移动，不是删除）；
- `rollback.json` 含 7 条 move、3 条 sidecar move、若干 `created_dirs`。

再次执行同一命令应为幂等：

```bash
python scripts/sk_entry.py --input /tmp/demo-apply --work /tmp/demo-work2 --out /tmp/demo-out2 --apply
# applied.status == "noop", moves == 0
```

## 4. 回滚

按 `/tmp/demo-work/rollback.json` 的 `moves` 反向移动即可；参考：

```bash
python - <<'PY'
import json, os
from pathlib import Path
root = Path('/tmp/demo-apply')
rb = json.load(open('/tmp/demo-work/rollback.json', encoding='utf-8'))
for m in rb['moves'] + rb['sidecar_moves']:
    src, dst = root / m['from'], root / m['to']
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
for d in sorted(rb['created_dirs'], key=len, reverse=True):
    try:
        (root / d).rmdir()
    except OSError:
        pass
print('rolled back')
PY
```

回滚后文件路径与内容应与原始 demo 一致（哈希可核对）。

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
