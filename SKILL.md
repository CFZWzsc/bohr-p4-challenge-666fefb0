---
name: research-literature-classifier
description: Use when a research-library case supplies a batch of papers, preprints, supplements, or datasets and asks to classify, tag, rename, deduplicate, or separate versions; run scripts/run_skill.py for a deterministic metadata-backed classification manifest with result.json.
---

# 文献与资料自动分类

## 用途与适用条件

在批量文献整理任务中使用：按文献类型/主题归入目录、生成可核对标签、按模板重命名、用内容哈希去重、区分预印本与正式版。输入为 `/input/case.json`、可选 `/input/task.md` 与真实文件批次。

不适用：单篇精读、翻译、综述写作、文献检索发现；也不对缺失的作者/年份/DOI 做推测填充。

## 输入

- `case.json`：`case_id`、`output_contract`；可选 `naming_template`（默认 `{author}-{year}-{title}`）与 `taxonomy`（类别列表）。
- 材料路径：`input_dir`、`materials`、`files`、`path` 等；缺失时回退 `/input`。
- 输出：`/app/output/result.json`，工作区 `/app/work`。

## 处理流程（对应评分维度）

1. **盘点与哈希（任务完成度）**：递归枚举文件，记录相对路径、字节、SHA-256、可读性；跳过目录与不可读文件并留痕。
2. **元数据抽取（结果正确性）**：DOI 用 `10.<4-9位>/...` 正则从文件名与文本头部抽取；年份取 1900–2099；作者仅在文件名符合 `姓-年份-...` 模式时抽取；标题来自文件名主干或文本首行。每个字段标记 `source`：`filename`、`text` 或 `null`，**缺失一律 `null`，不编造**。
3. **类型分类（结果完整性）**：先按文件名/文本关键词判定 `preprint`、`supplement`、`thesis`、`conference`、`dataset`、`review`、`article`、`unclassified`，无证据时归为 `unclassified` 并给出 `basis`。
4. **标签（质量）**：从标题与文本头部取词，去除停用词与纯数字，按词频与长度选最多 5 个标签，全部带命中依据；不做同义词臆测。
5. **重命名（可复核性）**：按模板生成 `author-year-title`，字段缺失降级为 `anon`/`nd`，标题截断 80 字符并清理非法字符，末尾附加 8 位内容哈希前缀防冲突；输出 `proposed_name` 与 `rename_reason`，不实际改名。
6. **去重与版本（正确性）**：SHA-256 相同为重复；文件名主干归一后同组但哈希不同为版本组，保留全部成员并标出当前版本。
7. **失败与恢复（可靠体验）**：不可读、加密、空文件、无文本层分别进入 `skipped`，不静默跳过。

## 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| `output_contract` 各字段 | 命中别名时填真实结果，否则 `null` + `contract_notes` |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | `assignments`、`tags`、`proposed_names`、`duplicates`、`version_groups`、`unresolved` |
| `evidence` | 哈希、字段来源、规则版本 |
| `uncertainty` | 缺失元数据、启发式分类 |
| `errors` / `stats` | 真实错误与计数 |

## 异常处理表

| 情况 | 处置 | 状态 |
|-|-|-|
| case.json 缺失/非法 | 空结果 + 错误 | `failed` |
| 无材料 | 空清单并说明 | `insufficient_input` |
| 元数据缺失 | 字段 `null`，`unresolved` 记录 | `partial` |
| 同名不同哈希 | 归入版本组，不覆盖 | `success` |
| 同哈希不同名 | 归入重复组，全部保留 | `success` |
| 文件不可读/加密 | `skipped` 记录原因 | `partial` |
| 分类无证据 | `unclassified` + `basis=no_evidence` | `partial` |

## 限制

- 不做 PDF 正文全文解析与作者消歧；作者、期刊、卷期常为 `null`。
- 标签是词频启发式，不是受控词表映射；跨语言同义词不合并。
- 不执行真实改名/移动，只输出可复核计划。

## 最小调用示例（示例，非官方案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

示意输入 `{"case_id":"synthetic-demo","output_contract":{"assignments":"array","tags":"array"}}`；示意输出保留 `case_id`、`assignments`、`tags` 与 `status`，无材料时为 `insufficient_input`。该示例仅为接口说明。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
