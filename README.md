# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-666fefb0` (track: `research-library`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `research-library-organizer`
- Summary: 将期刊论文、预印本、学位论文、补充材料与扫描件按证据分类，补全可证实元数据，统一重命名，检测重复与版本，默认复制到 /app/output/library（--dry-run 只出计划）并可回滚。

## Contents

| File |
|------|
| `SKILL.md` |
| `assets/policy.json` |
| `examples/demo-case/case.json` |
| `examples/demo-case/inbox/copy-of-journal-article-2023.pdf` |
| `examples/demo-case/inbox/journal-article-2023.pdf` |
| `examples/demo-case/inbox/journal-article-2023.pdf.meta.json` |
| `examples/demo-case/inbox/scan-2021-field-notes.pdf` |
| `examples/demo-case/inbox/smith-2020-graphene-formal.pdf` |
| `examples/demo-case/inbox/smith-2020-graphene-formal.pdf.meta.json` |
| `examples/demo-case/inbox/smith-2020-graphene-preprint.pdf` |
| `examples/demo-case/inbox/smith-2020-graphene-preprint.pdf.meta.json` |
| `examples/demo-case/inbox/supplement-S1.pdf` |
| `examples/demo-case/inbox/wang-2019-thesis.pdf` |
| `examples/demo-case/task.md` |
| `examples/quickstart.md` |
| `references/classification-rules.md` |
| `references/metadata-and-renaming.md` |
| `references/reversibility-and-failures.md` |
| `scripts/run_skill.py` |
| `scripts/sk_entry.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
