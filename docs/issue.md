# 目前问题
P1 Dense-only 命中会退化成 MySQL 预览内容
[chunk_hydrator.py (line 20)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/chunk_hydrator.py:20) 只对 row_map 缺失的 chunk 去 ES mget，但正常 dense 命中在 MySQL 里都有 manifest，于是 content = candidate.content or ... or chunk.content 会落到 [chunk_index_writer.py (line 75)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/chunk_index_writer.py:75) 写入的 320 字符 preview。Milvus-only 候选进入 LLM 时不是完整 chunk，违反方案里的 “hydrate chunk 内容”。应按“候选没有 content”去 ES 拉全文，而不是按“MySQL row 缺失”。

P1 COSINE 分数被反向转换，可能打乱 dense 排名
[milvus_store.py (line 284)](D:/agent/RAG/OmniAgent/src/backend/agentchat/infrastructure/vector_store/milvus_store.py:284) 对 COSINE 返回 1.0 - distance，随后 [hybrid_retriever.py (line 117)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/hybrid_retriever.py:117) 又按 dense_score 重新排序并重写 rank。Milvus 官方文档把 COSINE 定义为相似度，值越大越相似：Milvus Similarity Metrics。当前逻辑会把 0.9 变成 0.1，把 0.1 变成 0.9，dense RRF 排名会反向。

P1 索引失败不会阻止文档变 ready
[chunk_index_writer.py (line 87)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/chunk_index_writer.py:87) 先写 MySQL，再写 Milvus/ES；但 ES 写入失败在 [elasticsearch_store.py (line 173)](D:/agent/RAG/OmniAgent/src/backend/agentchat/infrastructure/search_store/elasticsearch_store.py:173) 被吞掉，Milvus 准备/连接失败也只返回空或 warning。随后 Worker 会在 [document_parse_worker.py (line 33)](D:/agent/RAG/OmniAgent/src/backend/agentchat/workers/document_parse_worker.py:33) 标记 job success、document ready。目标架构里 ready 应表示 MySQL/ES/Milvus 都完成，否则会出现“页面显示可问答，但实际召回缺一路甚至缺两路”。


# 解决思路
把三个 P1 统一成两条硬契约：
进入 LLM 的 SearchHit.content 必须是“可信全文”，不能静默使用 MySQL manifest preview。
document.status = ready 只在 MySQL manifest、Milvus、Elasticsearch 三路索引都成功后设置；写入链路失败必须抛出，让 worker 标记失败。
P1 Dense-only 内容退化
在 [chunk_hydrator.py (line 20)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/chunk_hydrator.py:20) 中把 hydrate 条件从“row_map 缺失”改成“候选没有 content”。
建议流程：
先按候选 chunk_id 查 MySQL，仍用于文档元数据、权限、页面号等 manifest。
计算 contentless_ids = candidates 中 content 为空的 chunk_id，对这些 id 执行 ES mget。
内容优先级改为：candidate.content（ES sparse 命中已有全文）→ es_contents[chunk_id]（dense-only hydrate）→ 安全 fallback。
fallback 不应默默把 320 字 preview 当全文：若能判断原 chunk 长度小于等于 preview 长度，可作为短文本使用。
否则跳过该候选，或在 metadata/debug 中标记 content_hydration_failed，不要进入 build_rag_prompt。

给 ChunkHydrator 增加 last_debug：requested_ids、mget_ids、hydrated_count、missing_content_ids、preview_fallback_count，并透传到 IndexingService.last_debug。
验收标准：Milvus-only 命中在 ES 存在全文时，SearchHit.content 等于全文；ES mget 失败时，不把 320 字截断内容静默喂给 LLM。
P1 COSINE 分数反向
在 [milvus_store.py (line 270)](D:/agent/RAG/OmniAgent/src/backend/agentchat/infrastructure/vector_store/milvus_store.py:270) 中把 _distance_to_score 改成按 metric 做“单调相似度”转换。
建议规则：
COSINE：Milvus 返回值越大越相似，直接返回原始值，不做 1.0 - distance。
IP：同样直接返回原始值。
L2：保持 1 / (1 + distance)，因为距离越小越相似。
方法名建议从 _distance_to_score 改为 _metric_value_to_score 或 _raw_metric_to_score，避免继续误导。
[hybrid_retriever.py (line 117)](D:/agent/RAG/OmniAgent/src/backend/agentchat/domains/indexing/hybrid_retriever.py:117) 后续按 dense_score 排序才会保持正确 dense rank。
验收标准：构造 Milvus COSINE raw score 0.9 和 0.1，dense 排名必须是 0.9 在前，RRF 的 dense_rank 也保持该顺序。
P1 索引失败仍 ready
把写入路径改成 fail-fast，读取/搜索路径可继续降级。
建议分层：
ElasticsearchChunkStore.upsert_chunks：ensure_index() 返回 false 时抛出索引写入异常。
bulk 写入必须校验成功数等于 action 数；partial failure 也算失败。
不再在写入路径吞异常，只记录日志后抛出。

MilvusVectorStore.upsert_chunks / upsert_chunks_incremental：vector dim 非法、连接失败、collection 准备失败、upsert 异常都抛出。
校验 vector_rows 与 embeddings 数量匹配，避免 zip 静默丢 chunk。

ChunkIndexWriter.index_chunks_batch：保持业务编排在 domain 层。
三路成功后才返回。
任一路失败时抛出统一异常，例如 IndexingWriteError，错误信息包含失败 backend、document_id、chunk count。

[document_parse_worker.py (line 33)](D:/agent/RAG/OmniAgent/src/backend/agentchat/workers/document_parse_worker.py:33) 现有 try/except 可以承接异常：索引失败后 job failed、document failed，不会再 ready。
可选更稳版本：引入 indexing / active chunk 状态或新 index_version 两阶段激活。先写新版本，Milvus/ES 成功后再把 chunk 标为 active 并置 document ready，旧版本最后清理。这样重建索引失败不会破坏旧 ready 文档。
验收标准：ES bulk 失败、ES disabled、Milvus 连接失败、Milvus collection 创建失败任一情况，都不能把文档标记为 ready。