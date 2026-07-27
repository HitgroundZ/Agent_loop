# RAG 黄金数据集

每个数据集使用独立目录，至少包含 `manifest.json` 和 UTF-8 `cases.jsonl`。

`manifest.json` 必须提供 `id`、`name`、`version` 和 `case_count`；目录名必须等于 `id`。建议在 `corpus.source_hashes` 中固定评测语料版本。

`cases.jsonl` 每行必须包含：

- `case_id`：数据集内唯一标识。
- `question`：发送给真实 Agent 的问题。
- `reference_answer`：Ragas context precision/recall 的参考答案。
- `reference_contexts`：至少一个黄金上下文，包含 `context_id`、`document_name` 和原文 `text`。
- `filters`：可选的 tenant、workspace、document、tag 和 subject 过滤条件。
- `tags`：可选案例分类。

`context_id` 是 `sha256:` 加规范化完整切片文本的 SHA-256。规范化规则是把连续空白折叠为一个空格并去掉首尾空白。后端在发起批次前会检查语料 source hash 和所有黄金 context ID；任何一个失效都会拒绝运行。

内置 `agent-loop-v1` 仅用于 smoke。形成正式业务指标前，应扩展为经过人工复核的代表性问题集，并通过新的版本号固定语料和标注。
