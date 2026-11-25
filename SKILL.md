---
name: research-library-organizer
description: 将期刊论文、预印本、学位论文、补充材料与扫描件按证据分类，补全可证实元数据，统一重命名，检测重复与版本，默认复制到 /app/output/library（--dry-run 只出计划）并可回滚。
---

# 科研资料批量整理

## 用途

把一个杂乱的科研资料目录整理成可复核的图书馆式结构：盘点文件、按证据分类、补全可证实元数据、统一重命名、识别重复下载与“预印本/正式版/修订版”关系。默认把结果**复制**到 `/app/output/library`，输入只读；`--dry-run` 只输出计划。绝不静默覆盖或删除。

## 适用条件

- 输入是目录或 `case.json`，内含 PDF、DOCX、TXT、HTML、图片扫描件等资料。
- 需要批量分类、补元数据、统一命名、去重、区分预印本与正式版。
- 允许“未知就留空”，不要求补全所有字段。
- 运行环境可执行 Python 3（仅标准库），不需要联网。

## 不适用场景

- 需要联网抓取 DOI/Crossref/PubMed 元数据（本 skill 明确不联网）。
- 需要 OCR 扫描件文字（本 skill 只识别“无文本层”，不生成 OCR 文本）。
- 需要解析加密 PDF、损坏压缩包或专有格式的正文。
- 需要删除文件、覆盖同名文件、做法律意义上的版本归档。
- 需要改动 `case.json` 或输入 sidecar 的语义内容。

## 输入形态

```
/input/
  case.json                     # 可选，推荐；提供 case_id、命名模板、taxonomy、output_contract
  task.md                       # 可选，任务说明，不计入资料清单
  <任意子目录>/
    paper.pdf
    paper.pdf.meta.json         # 可选 sidecar；也支持 <stem>.meta.json / .metadata.json / .sidecar.json / <stem>.json
    ...
```

`--input` 可以是目录，也可以直接指向 `case.json`。若 `case.json` 缺失或损坏，仍然盘点目录中的真实文件，并在 `errors`/`uncertainty` 中如实说明。

## 调用方式

在 skill 安装目录 `/skills/weekly-skill` 下执行（默认复制落库）：

```bash
python /skills/weekly-skill/scripts/sk_entry.py \
  --input /input \
  --work /app/work \
  --out /app/output
```

只看计划、不写输出时加 `--dry-run`：

```bash
python /skills/weekly-skill/scripts/sk_entry.py \
  --input /input \
  --work /app/work \
  --out /app/output \
  --dry-run
```

`--apply` 仍可传入，等价于默认行为；`--in-place` 保留旧的原地移动模式。

`run_skill.py` 是永不崩溃的薄封装，参数完全相同：

```bash
python /skills/weekly-skill/scripts/run_skill.py --input /input --work /app/work --out /app/output
```

本地演示：

```bash
python scripts/sk_entry.py \
  --input examples/demo-case \
  --work /app/work \
  --out /app/output
```

- `--input` 默认 `/input`；`--work` 默认 `/app/work`；`--out` 默认 `/app/output`。
- `--out` 可以是目录，也可以是 `.../result.json` 路径。
- `--apply` 默认生效；`--dry-run` 只输出计划，`--in-place` 才在输入目录内移动。
- 两个脚本都以退出码 0 结束，包括空输入、损坏输入和内部异常；异常会写进 `result.json` 的 `status` 与 `errors`。

## 分类层级与判定规则

默认层级（`assets/policy.json` 可覆盖）：

```
library/<document_type>/<year>/
library/archive/duplicates/
library/archive/versions/
```

- `document_type`：`journal_article`、`review`、`preprint`、`conference_paper`、`thesis`、`supplement`、`dataset`、`scan`、`unclassified`。
- `year` 取可证实年份；缺失时用 `unknown-year`，不猜。
- 重复文件进入 `archive/duplicates`；非保留版本进入 `archive/versions`，均保留原内容。

判定采用“证据加权、优先级确定”的规则，每条分类都写入 `classifications[].evidence`：

1. **权威声明**（权重 100）：`sidecar` 或 `case.json` 的 `type`/`document_type`/`kind`。
2. **原文文档类型标签**（85）：正文中的 `Document Type:` / `类型:`。
3. **文件名或标题标记**（52–68）：如 `preprint`、`supplement`、`thesis`、`conference`、`dataset`、`review`。
4. **预印本 DOI/站点**（55–62）：`10.1101/`、`10.21203/`、`arXiv`、`bioRxiv`、`medRxiv`、`SSRN` 等。
5. **正文标记**（42–54）：仅用于 thesis/conference/preprint/dataset；`supplement`/`review` 不因正文里出现 “supplementary bibliography”“peer reviewed” 等词而误判。
6. **结构特征**（24–30）：`abstract` + `references` → 期刊论文；`supervisor`/`degree`/`chapter` → 学位论文。
7. **无文本层**（36）：有效 PDF/图片且无可提取文字 → `scan`。
8. **扩展名兜底**（10）：PDF/DOC/DOCX 无更强证据时暂列 `journal_article`，并标为低置信度。
9. **提取失败**：损坏/加密导致无法提取 → `unclassified`，不冒充 `scan`。

`taxonomy` 若在 `case.json` 中给出，分类记录会同时给出映射后的 `category`，目录仍使用稳定的 `document_type` 层级。

## 元数据优先级与“未知保持未知”

逐字段按以下顺序取第一个非空值：

1. `sidecar`（`<file>.meta.json` 等），置信度 high；
2. `case.json` 的 `metadata` / `documents` / `files_metadata`，high；
3. 原文前几页可提取文本中的显式标签（`Title:`、`Authors:`、`Journal:`、`Year:`、`DOI:`），medium；
4. 文件名线索（作者-年份-标题、DOI），low。

规则：

- 没找到就是 `null`，并写入 `metadata[].unknown_fields` 与顶层 `uncertainty`。
- 不从 DOI 前缀臆测期刊，不从文件名臆测作者全名，不补造 DOI。
- `authors` 只接受 sidecar/case.json 的显式列表或原文 `Authors:` 标签；文件名只提供低置信度单作者线索。
- `provenance` 逐字段记录来源与置信度。
- 中文乱码会尝试 `utf-8`、`gb18030`、`big5` 等解码，仍不可读时如实标注，不强行编造。

## 重命名模板

默认模板：`{first_author}-{year}-{title}`，可在 `case.json` 的 `naming_template` 覆盖。

支持的占位符：`{first_author}`、`{author}`、`{year}`、`{title}`、`{journal}`、`{doi}`、`{type}`、`{document_type}`、`{ext}`。

- 未知作者 → `anon`；未知年份 → `nd`；未知标题 → `untitled`；未知期刊 → `unknown-journal`；未知 DOI → `no-doi`。
- 组件会做 Unicode NFC、非法字符替换、空白转连字符、Windows 保留名规避、字节长度截断。
- 扩展名保留。
- 目标重名或目标已存在且内容不同 → 追加 `__<sha256前8位>`，绝不覆盖；冲突写入 `conflicts`。
- 归档副本追加 `__dup-<hash>`，非保留版本追加 `__<variant>-<hash>`。
- 目标已存在且内容相同 → 跳过复制并记为 `applied`；内容不同则追加 `__<sha256前8位>`，绝不覆盖。

## 重复与版本判定规则

**重复**：完整文件 SHA-256 相同（`sha256_scope=full`）即同一组。

- keeper 优先：正式类型、非 archive 路径、非 “copy/副本/(1)” 名称、带 sidecar、路径更短、字典序更小。
- 其余成员复制到 `archive/duplicates/`，不删除任何源文件。
- 哈希为 partial 的文件不参与重复判定。

**版本**：

- 按相同 DOI 分组；再按“规范化标题 + 第一作者 + 年份”分组；重叠组合并。
- 变体：`preprint`、`formal`、`revision`、`supplement`、`other`。
- 同时存在 preprint 与 formal/revision → `preprint_vs_formal`；存在 revision 标记 → `revision`；否则 `same_work`。
- keeper 优先正式版，其次修订版，再预印本；其余进入 `archive/versions/`。
- 分组键、成员、关系、keeper 全部写入 `versions[]`，便于人工复核；这是启发式，`uncertainty` 会明示。

## 可逆性

- 每次运行都在 `--work` 下写 `manifest.json`，并在 `--out` 下写 `rollback.json`。
- `result.json` 顶层包含 `rollback_plan`：`moves`（输出副本→原输入路径）、`sidecar_moves`、`created_files`、`created_dirs`。
- 默认复制：从输入读取后写入 `<out>/library/...`，逐字节校验 `target_sha256`；输入目录不被修改。
- 只创建目录、复制文件；从不覆盖、不删除内容文件。`--in-place` 才移动改名。
- 回滚时删除 `rollback_plan.created_files` 中的输出副本，再删除 `created_dirs` 中已空的目录；输入无需恢复。
- 重复执行是幂等的：目标已存在且 hash 相同则跳过复制，仍记为 `applied`，输出不增长。

## 先 dry-run

`--dry-run` 只出计划：

- 不建目录、不复制、不改名、不写输入目录。
- 仍会计算哈希、读取文本、生成完整计划与 `rollback_plan`（状态 `planned`）。
- 自检应确认输入文件的 SHA-256 与 mtime 在 dry-run 前后完全一致。

## 异常处理

| 情况 | 行为 |
|---|---|
| 损坏/截断 PDF | `text_status=error`，类型 `unclassified`，`skipped` 记录 `text_extraction_error`，不中断 |
| 加密 PDF | `text_status=encrypted`，不尝试破解，标 `unclassified` |
| 扫描件/图片无文本层 | 类型 `scan`，`skipped` 记录 `text_extraction_no_text`，仍可按文件名/年份生成低置信度命名 |
| 零字节文件 | `skipped` 记录 `text_extraction_empty`，`unclassified` |
| 同名不同源 | 目标冲突 → 追加哈希后缀，原文件不动，写 `conflicts` |
| 目标已存在同内容 | 标 `already_present`，源文件保留，不覆盖 |
| 权限不足 | 盘点中保留记录，`readable=false`，`skipped` 记录 `permission_denied` |
| 超大文件 | 超过 `max_file_bytes` 时哈希标 `partial`，不参与重复/版本判定 |
| 符号链接 | 不跟随，`skipped` 记录 `symlink_skipped` |
| 字符/路径限制 | 非法字符替换、保留名规避、按 UTF-8 字节截断、超长标题截断 |
| 中文乱码 | 多编码尝试并选最优；仍异常时在文本详情中标注 |
| case.json 缺失/损坏 | 继续处理目录，`status=partial`，`errors` 记录原因 |
| 空目录 | `status=insufficient_input`，退出码 0，结果结构完整 |

## 输出契约

`result.json` 始终包含：

- `case_id`、`status`、`mode`；
- `inventory`、`classifications`、`metadata`、`renames`、`duplicates`、`versions`、`conflicts`、`skipped`、`plan_actions`、`applied`、`rollback_plan`、`evidence`、`uncertainty`、`run_log`、`errors`、`summary`、`state`；
- 别名：`files`/`items` → `inventory`；`categories`/`labels` → `classifications`；`moves` → `renames`；`dupes` → `duplicates`；`skips` → `skipped`；
- 额外：`tags`、`directories`、`auxiliary`、`metadata_index`、`contract_coverage`、`contract_extras`。

`case.json` 的 `output_contract` 无论写成对象、字符串列表还是 `[{name: ...}]`，每个键都会出现在结果中：

- 已知键按直接/别名映射；
- 名称相近的键按 token 模糊映射；
- 完全未知的键写入一个非空 marker 对象，并在 `contract_extras` 说明“无生产者”。

## 自检清单

- [ ] `python -m py_compile scripts/sk_entry.py scripts/run_skill.py` 通过。
- [ ] 默认运行退出码 0，`result.json` 存在且含全部 `output_contract` 键。
- [ ] `inventory` 覆盖输入中的全部资料文件；sidecar/case.json 在 `auxiliary`。
- [ ] 每条分类都有 `evidence`；低置信度在 `confidence` 标出。
- [ ] 缺失元数据为 `null`，并在 `unknown_fields`/`uncertainty` 说明，无编造。
- [ ] 重复组 keeper 与成员正确；预印本/正式版组关系为 `preprint_vs_formal`。
- [ ] 运行前后输入文件哈希与 mtime 完全一致。
- [ ] 默认运行后 `<out>/library` 的 7 个文件存在、`moves` 全部 `applied`、`applied.inputs_modified=false`。
- [ ] 重跑不重复复制，`result.json` 字节一致。
- [ ] 空目录/损坏输入退出码 0，`status` 如实为 `insufficient_input`/`partial`。
- [ ] 无 `__pycache__`、无符号链接、无网络调用。

## 给 Agent 的执行说明

1. **默认运行复制落库**：输入只读，产物写入 `<out>/library`；只需预览时加 `--dry-run`。
2. **读 `result.json`**：确认 `status`、`case_id`、`inventory` 数量，并逐项核对 `classifications[].evidence`、`metadata[].provenance`、`duplicates`、`versions`、`conflicts`、`skipped`。任何 `null` 都表示“未知”，不要补写。
3. **把 manifest 与文件系统对账**：只读校验，不修改输入。可用：

   ```bash
   python - <<'PY'
   import json, os
   r = json.load(open('/app/output/result.json', encoding='utf-8'))
   root = r['evidence']['input_root']
   on_disk = set()
   for d, _, fs in os.walk(root):
       for f in fs:
           on_disk.add(os.path.relpath(os.path.join(d, f), root).replace('\\', '/'))
   listed = {x['path'] for x in r['inventory']}
   aux = {x['path'] for x in r.get('auxiliary', [])}
   print('missing_from_inventory', sorted(on_disk - listed - aux))
   print('listed_but_absent', sorted(listed - on_disk))
   print('duplicate_groups', len(r['duplicates']), 'version_groups', len(r['versions']))
   print('unknown_metadata_files', [m['file'] for m in r['metadata'] if m['unknown_fields']])
   PY
   ```

   预期：`missing_from_inventory` 与 `listed_but_absent` 均为空；sidecar/case.json 允许在 `auxiliary`。
4. **核对落库结果**：检查每个 `renames[].target`（输出侧 `library/...`）存在、`target_sha256` 与文件一致、`applied.failures` 为空、`applied.inputs_modified=false`、`rollback_plan` 完整；确认输入文件 hash 未变。
5. **写 `/app/output/run-review.md`**，至少包含：
   - 结论与 `status`、`case_id`、文件总数/字节数；
   - 分类依据表：文件 → 类型 → 置信度 → 关键证据；
   - 元数据缺口：哪些字段为 `null`、来源是什么、为何不补；
   - 重复与版本：组键、keeper、被归档成员；
   - 冲突与跳过：原因与“未覆盖/未删除”的处理；
   - 可逆性：`rollback.json` 路径、回滚条目数、当前模式（默认复制 / `--dry-run` / `--in-place`）；
   - 未决问题与不确定性（直接引用 `uncertainty`，不要自行“修复”事实）。
6. **报告口径**：明确区分“已执行”和“仅计划”；不要把 `plan_actions` 说成已完成；不要声称做了 OCR 或联网核验。
