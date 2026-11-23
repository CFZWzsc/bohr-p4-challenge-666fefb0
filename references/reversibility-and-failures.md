# 可逆性与异常处理

## Dry-run 是默认

不加 `--apply` 时：

- 不创建目录；
- 不移动、不改名、不写 sidecar；
- 仍计算哈希、提取文本、生成分类、重复/版本组、完整计划和 `rollback_plan`（`status=planned`）；
- 输入文件的 SHA-256 与 mtime 应完全不变。

自检建议：运行前后对输入做哈希快照，差异必须为空。

## Apply 的两阶段移动

`--apply` 时：

1. 先把每个源文件移动到 `--work/staging/<序号>_<文件名>`；
2. 创建目标目录；
3. 再把 staging 文件移动到目标路径；
4. 移动对应 sidecar 到 `<target>.meta.json` 等位置；
5. 阶段失败时尽量把 staging 文件移回原路径。

这样可以安全处理“A 的目标是 B 的当前路径”这类链式/循环命名，不会误覆盖。

## 产物

`--work` 下：

- `manifest.json`：盘点、分类、元数据、重命名、重复、版本、冲突、跳过、计划、证据；
- `rollback.json`：可直接用于回滚的移动清单；
- `staging/`：仅 apply 过程中短暂存在，成功后清空。

`--out` 下：

- `result.json`：最终结果，含全部契约键与 `rollback_plan`。

## 回滚清单结构

```json
{
  "mode": "apply",
  "status": "recorded",
  "moves": [{"from": "library/.../new.pdf", "to": "inbox/old.pdf", "sha256": "..."}],
  "sidecar_moves": [{"from": "library/.../new.pdf.meta.json", "to": "inbox/old.pdf.meta.json"}],
  "created_dirs": ["library/journal_article/2023"],
  "instructions": ["..."]
}
```

回滚步骤：

1. 按 `moves` 反向移动（`from` → `to`）；
2. 按 `sidecar_moves` 反向移动；
3. 仅当目录为空时删除 `created_dirs` 中的目录；
4. 不删除任何内容文件。

## 幂等性

- 已处于目标路径 → `already_in_place`，不产生移动。
- 第二次 `--apply` 预期：`applied.status = "noop"`、`applied.moves = 0`。
- 重复检测仍会报告已有的重复组；这是事实，不代表再次移动。

## 异常与降级

| 异常 | 结果字段 | 是否中断 |
|---|---|---|
| case.json 缺失 | `status=partial`，`errors` 记录 | 否，继续处理目录 |
| case.json 损坏 | `status=partial`，`errors` 记录 | 否 |
| 空目录/无资料 | `status=insufficient_input` | 否，退出码 0 |
| PDF 头损坏 | `skipped[].reason_code=text_extraction_error`，类型 `unclassified` | 否 |
| 加密 PDF | `text_status=encrypted`，`unclassified` | 否 |
| 扫描件/图片 | 类型 `scan`，`skipped[].reason_code=text_extraction_no_text` | 否 |
| 零字节文件 | `skipped[].reason_code=text_extraction_empty` | 否 |
| 权限不足 | `readable=false`，`skipped[].reason_code=permission_denied` | 否 |
| 符号链接 | `skipped[].reason_code=symlink_skipped` | 否 |
| 超大文件 | `sha256_scope=partial`，不参与重复/版本 | 否 |
| 目标同名不同源 | `conflicts[].kind=target_exists_other_content`，哈希后缀 | 否 |
| 目标同内容 | `conflicts[].kind=target_exists_same_content`，源保留 | 否 |
| 坐标/路径过长 | 组件截断到字节上限 | 否 |
| 内部异常 | `status=failed`，`errors` 记录；仍写 result.json | 否，退出码 0 |

## 不可恢复的情况

- 运行环境没有写权限：无法写 `result.json`，但脚本仍返回 0 并向 stderr 说明。
- 输入在运行中被外部删除：`apply` 记录 `source disappeared before apply`，不虚构成功。
- 需要 OCR 或联网补元数据：明确不做，写入 `uncertainty`，交给人工或其他工具。
