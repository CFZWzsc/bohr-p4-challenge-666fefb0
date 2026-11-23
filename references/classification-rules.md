# 分类规则

## 目标层级

```
library/<document_type>/<year>/
library/archive/duplicates/
library/archive/versions/
```

- `year` 缺失时使用 `unknown-year`。
- `case.json` 的 `taxonomy` 只影响 `classifications[].category`，不影响稳定的目录层级。
- 归档目录用于“保留但不作为主副本”的重复件与非保留版本。

## 文档类型

| 类型 | 典型证据 | 目标目录 |
|---|---|---|
| `journal_article` | sidecar/case.json 类型；期刊 DOI/刊名；abstract+references | `library/journal_article/<year>/` |
| `review` | 文件名/标题含 review、survey、meta-analysis | `library/review/<year>/` |
| `preprint` | `10.1101/`、arXiv、bioRxiv、medRxiv、SSRN；类型声明 | `library/preprint/<year>/` |
| `conference_paper` | proceedings、conference、symposium、workshop | `library/conference_paper/<year>/` |
| `thesis` | thesis、dissertation、doctoral、degree、supervisor | `library/thesis/<year>/` |
| `supplement` | supplement、supplementary information、supporting information、附录 | `library/supplement/<year>/` |
| `dataset` | dataset、Zenodo、figshare、Dryad | `library/dataset/<year>/` |
| `scan` | 有效 PDF/图片但无文本层 | `library/scan/<year>/` |
| `unclassified` | 无证据、损坏或加密导致不能判断 | `library/unclassified/<year>/` |

## 证据权重

脚本对每个文件收集全部信号，按权重降序、同权重按固定优先级选最高者。所有信号写入 `classifications[].evidence`。

| 权重 | 来源 | 说明 |
|---|---|---|
| 100 | sidecar / case.json | `type`、`document_type`、`kind`，最权威 |
| 85 | 原文标签 | `Document Type:` / `类型:` |
| 68 左右 | 文件名/标题 marker | 如 `supplement`、`thesis`、`conference`、`preprint`、`dataset` |
| 62 左右 | 预印本站点/DOI | bioRxiv、medRxiv、arXiv、`10.1101/` 等 |
| 42–54 | 正文 marker | 仅 thesis/conference/preprint/dataset；supplement/review 不扫描正文，避免 “supplementary bibliography”“peer reviewed” 误判 |
| 24–30 | 结构 | abstract+references；supervisor/degree/chapter |
| 36 | 无文本层 | 有效 PDF/图片，且无更强信号 → scan |
| 10 | 扩展名兜底 | PDF/DOC/DOCX，无更强证据 → journal_article（低置信度） |
| 15 | 提取失败 | 损坏/加密 → unclassified，不冒充 scan |

置信度映射：

- `high`：权重 ≥ 80；
- `medium`：权重 40–79；
- `low`：权重 < 40。

## 判定顺序（概念上）

1. 权威类型声明；
2. 原文类型标签；
3. 预印本 DOI/站点；
4. 文件名/标题 marker；
5. 正文 marker 与结构；
6. 无文本层 → scan；
7. 扩展名兜底；
8. 仍无证据 → unclassified。

同一文件可以同时有多个信号；最终类型取最高权重。例如：

- 文件名 `supplement-S1.pdf` 与 case.json `type=supplement`：case.json 100 胜出。
- 文件名 `smith-2020-graphene-preprint.pdf` + bioRxiv DOI：两个 preprint 信号一致，仍为 preprint。
- 正文里出现 “supplementary bibliography” 但文件名/标题无 supplement：不判 supplement。
- 有效 PDF 但 `stream` 中没有文字：scan，并在 `skipped` 记录 `text_extraction_no_text`。
- PDF 头损坏：unclassified，`skipped` 记录 `text_extraction_error`。

## 无证据时

- 不把“看起来像论文”当成证据。
- `unclassified` 不是失败，是诚实状态；写入 `uncertainty`，等待人工复核。
- `confidence=low` 的分类保留在结果中，但 `status` 通常为 `partial`。
