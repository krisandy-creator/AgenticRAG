# RAG 问答与解析可视化修复说明

## 更新日期

2026-06-30

## 文档解析详情

`GET /api/v1/documents/{document_id}/parse-job` 在原有解析任务状态基础上，新增 `trace` 字段：

- `summary.page_count`：解析出的页面数。
- `summary.chunk_count`：切分出的 chunk 数。
- `summary.ocr_page_count`：包含 OCR block 的页面数。
- `summary.text_char_count`：解析文本字符数。
- `summary.parser_counts`：按解析器统计的 chunk 数。
- `summary.chunk_type_counts`：按 chunk 类型统计的数量。
- `events`：接收文件、解析文本、切分 chunk、写入索引四个阶段状态。
- `pages`：页面级 OCR/block 摘要。
- `chunks`：前 8 个 chunk 的摘要样例。

前端文档管理页通过“解析详情”按钮展示以上信息。

## 问答流式事件

`POST /api/v1/chat/stream` 仍然返回 SSE，但前端只把 `answer_delta` 追加到回答正文。

其他事件用于链路展示：

- `prompt_built` 不再向前端返回完整 prompt，只返回证据数量、prompt 长度和提示词策略摘要。
- `citation` 返回来源元信息，包括 `document_id`、`file_name`、`page_no`、`chunk_id`、`source_label`。
- 最终回答下方展示“引用来源”链接，正文不粘贴引用原文。

## 回答生成约束

RAG 提示词要求模型先筛选关键证据，再输出结论、必要依据和行动建议；不得逐条复述检索片段，也不得把原文大段粘贴到回答正文中。
