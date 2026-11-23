# 元数据与重命名

## 逐字段优先级

脚本对每个字段独立取值，先到先得：

| 优先级 | 来源 | 置信度 | 说明 |
|---|---|---|---|
| 1 | sidecar | high | `<file>.meta.json`、`<stem>.metadata.json`、`<stem>.sidecar.json`、`<stem>.json` |
| 2 | case.json | high | `metadata` / `documents` / `files_metadata` / `items` / `file_metadata` |
| 3 | 原文文本 | medium | 前几页/前 1 MB 的显式标签 |
| 4 | 文件名线索 | low | 作者-年份-标题、DOI |

字段：`title`、`authors`、`year`、`journal`、`doi`、`volume`、`issue`、`pages`、`publisher`、`language`。

每条元数据记录包含：

```json
{
  "file": "inbox/paper.pdf",
  "title": "...",
  "authors": ["..."],
  "year": 2023,
  "journal": "...",
  "doi": "10....",
  "volume": null,
  "issue": null,
  "pages": null,
  "publisher": null,
  "language": "en",
  "first_author": "...",
  "provenance": {"title": {"source": "sidecar", "confidence": "high"}},
  "unknown_fields": ["volume", "issue"],
  "notes": []
}
```

## 不编造规则

- 找不到就是 `null`，并进入 `unknown_fields`；顶层 `uncertainty` 汇总缺失情况。
- 不从 DOI 前缀推期刊（`10.1038` 不等于一定是 Nature）。
- 不从文件名推完整作者列表；文件名最多提供一个低置信度单作者线索。
- 不从正文任意句子猜标题；只接受 `Title:` 标签或首行标题特征。
- 不把“未知”改写成 `unknown` 字符串；保持 `null`。
- 作者的 `Last, First` 与 `First Last` 都按显式分隔符处理；不猜测机构、通讯作者、ORCID。

## 文件名解析

支持：

- 年份 token：`2019`、`2020`；
- 作者：年份前一个非停用词 token；
- 标题：年份后的 token，或年份前除作者/停用词外的 token；
- DOI：`10.xxxx/...`，也支持 `10.xxxx_...` 的文件名写法；
- 自动去除 `copy of`、`(1)`、`preprint`、`thesis`、`supplement`、`final`、`rev` 等标记。

标题质量门槛：至少 4 个字符、非泛化词（`article`、`paper`、`supplement` 等）、非 DOI 本身。否则留空。

## 命名模板

默认：`{first_author}-{year}-{title}`。

可用占位符：

- `{first_author}` / `{author}`
- `{year}`
- `{title}`
- `{journal}`
- `{doi}`
- `{type}` / `{document_type}`
- `{ext}`

缺省值：`anon`、`nd`、`untitled`、`unknown-journal`、`no-doi`。

## 字符与路径处理

- Unicode NFC 规范化。
- `\ / : * ? " < > |` 与控制字符替换为 `-`。
- 空白折叠为 `-`，保留中文。
- 去除首尾空格、点、连字符。
- Windows 保留名（`CON`、`PRN`、`AUX`、`NUL`、`COM1`…）前加 `_`。
- 组件按 UTF-8 字节截断（默认 180 字节），避免损坏多字节字符。
- 扩展名保留，使用原文件小写扩展名。

## 冲突与后缀

- 两个源文件提出同一目标：后来者追加 `__<sha256前8位>`。
- 目标已存在且内容不同：追加哈希后缀，原文件不动。
- 目标已存在且内容相同：标 `already_present`，不移动源文件、不删除。
- 重复副本：`__dup-<hash>`；非保留版本：`__<preprint|revision|version>-<hash>`。
- 已处于目标路径：标 `already_in_place`，不产生移动操作。

## 示例

```text
inbox/journal-article-2023.pdf
  sidecar: title=Deep Learning for Materials Discovery, authors=[Wei Chen, Maria Lopez], year=2023
  → library/journal_article/2023/Wei-Chen-2023-Deep-Learning-for-Materials-Discovery.pdf

inbox/wang-2019-thesis.pdf
  原文: Title: Large-Scale Graph Neural Networks..., Author: Li Wang, Year: 2019
  → library/thesis/2019/Li-Wang-2019-Large-Scale-Graph-Neural-Networks....pdf
  journal/doi 未知 → null
```

## provenance 含义

- `sidecar` / `case_json`：显式机器可读元数据。
- `embedded_text`：从 PDF/DOCX/文本中提取的标签或首行。
- `filename_hint`：仅文件名推断，必须低置信度。
- `unknown`：字段有值但来源未登记（正常路径不应出现，出现时应人工复核）。
