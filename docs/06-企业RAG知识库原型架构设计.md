# 企业 RAG 知识库第一版原型架构设计

> 版本：v0.1  
> 创建日期：2026-06-30  
> 阶段：第一版原型设计  
> 项目目标：基于 OmniAgent 仓库进行破坏性重构，落地企业内部员工使用的 RAG 知识库原型

---

## 1. 原型目标

第一版原型只验证一条核心闭环：

```text
管理员上传文档
-> OSS 保存源文件
-> 异步解析文档
-> 生成 chunk 与语义索引
-> Embedding 后写入本地 Milvus
-> 员工提问
-> 实时展示检索链路
-> Rerank 后生成带源文件引用的回答
-> Trace 详情可回放
```

原型先做“能跑通、能演示、边界清楚”，不追求企业级完整能力。

---

## 2. 已确认决策

| 主题 | 第一版决策 |
|------|------------|
| 用户 | 企业内部员工 |
| 知识库 | 全局知识库 |
| 前端页面 | 登录、文档管理、问答、Trace 详情 |
| 文档权限 | 管理员上传时选择权限等级，检索时按 `用户角色等级 >= 文档权限等级` 过滤 |
| 普通用户 | 文档管理页只读，不能上传 |
| 管理员 | 可上传、删除、查看所有文档 |
| 文档类型 | docx、图文 PDF、Excel |
| docx | 纯文本解析，传统 chunk + overlap |
| 图文 PDF | 多模态模型逐页解析，输出页面 Markdown、结构化块和页码 |
| Excel | 不落结构化库，生成语义 Markdown 索引；计算时通过 CLI / pandas 读取源文件 |
| 源文件存储 | OSS |
| 向量库 | 本地轻量级 Milvus |
| Embedding | 沿用现有 embedding 服务 |
| Rerank | 第一版必须支持 |
| 问答模式 | 用户手动选择“普通问答 / 深度分析” |
| Trace | 按 SSE 事件流全量保存 |
| Skill | 第一版不做，仅保留后续扩展抽象 |
| Multi-agent | 第一版不做，仅让 workflow 结构可扩展多个节点 |

---

## 3. 暂不做范围

- 多知识库
- 版本管理
- 文件更新
- 召回率评测
- Excel 字段对齐
- PDF 点击定位到页内坐标
- Skill 系统
- Multi-agent 编排
- MCP、Text2SQL、CodeAct、原七类 Agent 展示

---

## 4. 现有项目取舍

### 4.1 保留思路，建议重写实现

| 模块 | 判断 |
|------|------|
| FastAPI 应用入口 | 保留技术栈和路由组织思路，重建业务路由 |
| Vue 3 + Element Plus | 保留技术栈，重建页面 |
| 模型配置 | 保留“模型基础设施”方向，抽象为模型供应商端口 |
| OSS / MinIO 存储 | 保留适配器思路，第一版使用 OSS |
| JWT 登录 | 保留认证思路，重建角色权限模块 |

### 4.2 必须重写

| 模块 | 原因 |
|------|------|
| RAG 主链路 | 当前链路返回拼接字符串，丢失 chunk、页码、来源和 trace |
| 文档解析 pipeline | 当前 PDF、docx、Excel 解析方式不满足原型目标 |
| 知识库数据模型 | 缺少文档权限、解析任务、chunk metadata、trace |
| 会话系统 | 需要保存检索过程、引用证据、完整事件流 |
| 权限系统 | 需要从“资源所有者”转为“角色等级 + 文档权限等级” |

### 4.3 下线

- Agent 管理页面与七类 Agent 实现
- MCP 管理与 MCP 对话
- Tool 管理
- Text2SQL、CodeAct
- Mars、Lingseek、DeepSearch 教学演示
- 旧知识库页面的大部分交互

---

## 5. DDD 边界

第一版按 5 个核心域组织，不做过细拆分。

| 领域 | 职责 |
|------|------|
| `identity` | 用户、角色、角色等级、管理员判断 |
| `documents` | 文档、上传、OSS、文档权限等级、解析任务 |
| `indexing` | chunk、metadata、embedding、Milvus 写入与检索 |
| `rag` | 普通问答、深度分析、query rewrite、retrieval、rerank、Excel 计算工具 |
| `observability` | Trace、事件流、prompt、模型响应、引用证据 |

核心原则：

- 业务域不直接依赖 OSS、Milvus、模型 SDK。
- 基础设施通过端口接口接入。
- SSE 事件和 Trace 事件使用同一份事件模型。
- 第一版 workflow 节点化，但不引入 multi-agent。

---

## 6. 建议后端目录结构

```text
src/backend/agentchat/
├── api/
│   └── v1/
│       ├── auth.py
│       ├── documents.py
│       ├── chat.py
│       └── traces.py
│
├── domains/
│   ├── identity/
│   │   ├── entities.py
│   │   ├── repositories.py
│   │   └── services.py
│   │
│   ├── documents/
│   │   ├── entities.py
│   │   ├── repositories.py
│   │   ├── services.py
│   │   └── jobs.py
│   │
│   ├── indexing/
│   │   ├── entities.py
│   │   ├── chunking.py
│   │   ├── repositories.py
│   │   └── services.py
│   │
│   ├── rag/
│   │   ├── workflow.py
│   │   ├── normal_rag.py
│   │   ├── deep_analysis.py
│   │   ├── excel_tool.py
│   │   └── prompts.py
│   │
│   └── observability/
│       ├── entities.py
│       ├── event_schema.py
│       ├── repositories.py
│       └── services.py
│
├── infrastructure/
│   ├── db/
│   ├── storage/
│   │   └── oss_storage.py
│   ├── vector_store/
│   │   └── milvus_store.py
│   ├── models/
│   │   ├── chat_model_provider.py
│   │   ├── embedding_provider.py
│   │   └── rerank_provider.py
│   └── excel/
│       └── pandas_cli_runner.py
│
├── workers/
│   └── document_parse_worker.py
│
└── main.py
```

说明：

- 保留 `agentchat` 包名，降低启动脚本和导入路径的迁移成本。
- 原有 `core/agents`、`services/mcp`、`tools` 后续可删除或归档。
- 第一版可以先用数据库轮询或 FastAPI background task 做异步解析，后续再换 Celery / RQ。

---

## 7. 基础设施端口

### 7.1 StoragePort

```python
class StoragePort:
    async def upload(self, object_key: str, data: bytes) -> str: ...
    async def download(self, object_key: str) -> bytes: ...
    async def presigned_url(self, object_key: str, expires: int = 3600) -> str: ...
```

第一版实现：`OssStorageAdapter`。

### 7.2 VisionParsePort

```python
class VisionParsePort:
    async def parse_pdf(self, file_bytes: bytes) -> list["VisionPage"]: ...
```

第一版实现：渲染 PDF 页面后调用多模态模型解析。

### 7.3 VectorStorePort

```python
class VectorStorePort:
    async def upsert_chunks(self, chunks: list["IndexedChunk"]) -> None: ...
    async def search(self, query_vector: list[float], filters: dict, top_k: int) -> list["SearchHit"]: ...
```

第一版实现：本地 Milvus。

### 7.4 ModelProvider

```python
class EmbeddingProvider:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

class RerankProvider:
    async def rerank(self, query: str, docs: list[str], top_k: int) -> list["RerankResult"]: ...

class ChatModelProvider:
    async def stream_answer(self, messages: list[dict]) -> AsyncIterator[str]: ...
```

第一版沿用现有模型服务配置，但业务代码只依赖端口。

---

## 8. 文档解析 Pipeline

### 8.1 总流程

```text
上传文件
-> 计算内容 hash
-> OSS 保存源文件
-> 创建 document
-> 创建 parse_job
-> 异步 worker 拉取任务
-> 按文档类型解析
-> 生成 chunk
-> embedding
-> 写入 Milvus
-> document.status = ready
```

### 8.2 docx

```text
docx
-> 抽取纯文本
-> 按段落聚合
-> chunk + overlap
-> metadata: document_id, file_name, file_type, permission_level
```

第一版不处理 docx 页码。

### 8.3 图文 PDF

```text
pdf
-> 渲染页面为图片
-> 调用多模态模型解析页面标题、正文、表格、流程和图示
-> 生成页面 Markdown 与结构化 chunks
-> metadata: document_id, page_no, parser=qwen_vl, permission_level
```

用户引用展示到页码级；结构化字段进入 metadata，供后续检索和 Trace 展示使用。

### 8.4 Excel

```text
excel
-> 读取 workbook / sheet
-> 提取 sheet 名、表头、字段类型、样例值、行列数量
-> 生成语义 Markdown
-> Markdown chunk
-> embedding
-> 写入 Milvus
```

Excel 不做事实表落库。需要计算时：

```text
检索命中 Excel 索引
-> 下载 OSS 源文件到临时目录
-> 由受控 pandas CLI 执行计算
-> 结果写入 Trace
-> 回答引用源文件
```

---

## 9. Milvus 索引模型

第一版使用一个全局 collection。

```text
collection: global_knowledge_chunks

fields:
- id: varchar, primary key
- vector: float_vector
- document_id: varchar
- chunk_id: varchar
- file_name: varchar
- file_type: varchar
- content: varchar
- page_no: int, nullable
- permission_level: int
- chunk_type: varchar
- metadata_json: varchar/json
- created_at: datetime
```

检索过滤：

```text
permission_level <= user_role_level
document_status = ready
```

如果 Milvus 当前版本的 metadata filter 能力有限，第一版可以扩大召回数量，再在应用层过滤，但默认优先在向量检索阶段过滤。

---

## 10. 核心数据表草图

### 10.1 用户与角色

```text
user
- user_id
- user_name
- user_password
- is_deleted
- created_at
- updated_at

role
- role_id
- role_name
- access_level
- created_at
- updated_at

user_role
- id
- user_id
- role_id
```

`access_level` 用简单大小比较，不做复杂 ACL。

### 10.2 文档

```text
document
- document_id
- file_name
- file_type
- content_hash
- file_size
- oss_key
- permission_level
- status: uploaded | parsing | ready | failed
- uploaded_by
- created_at
- updated_at
```

### 10.3 解析任务

```text
document_parse_job
- job_id
- document_id
- status: pending | running | success | failed
- parser_type: docx | pdf_multimodal | excel_markdown
- error_message
- started_at
- finished_at
- created_at
```

### 10.4 页与 chunk

```text
document_page
- page_id
- document_id
- page_no
- text
- blocks_json
- created_at

document_chunk
- chunk_id
- document_id
- page_no
- chunk_type: text | vision_page | vision_fact | vision_table_row | vision_step | vision_figure | excel_markdown
- content
- vector_id
- permission_level
- metadata_json
- created_at
```

### 10.5 问答与 Trace

```text
chat_session
- session_id
- user_id
- title
- created_at
- updated_at

chat_message
- message_id
- session_id
- role: user | assistant
- content
- trace_id
- created_at

trace
- trace_id
- session_id
- user_id
- question
- mode: normal | deep_analysis
- status: running | success | failed
- created_at
- finished_at

trace_event
- event_id
- trace_id
- seq
- event_type
- payload_json
- created_at
```

---

## 11. API 草图

### 11.1 认证

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

### 11.2 文档管理

```text
GET    /api/v1/documents
POST   /api/v1/documents/upload
GET    /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}
GET    /api/v1/documents/{document_id}/download-url
GET    /api/v1/documents/{document_id}/parse-job
```

权限：

- 管理员：可上传、删除、查看全部。
- 普通员工：只读，且只能看到权限等级允许的文档。

### 11.3 问答

```text
POST /api/v1/chat/sessions
GET  /api/v1/chat/sessions
GET  /api/v1/chat/sessions/{session_id}/messages
POST /api/v1/chat/stream
```

`/chat/stream` 返回 SSE。

请求示例：

```json
{
  "session_id": "s_xxx",
  "question": "统计这些销售表中各区域的合同金额",
  "mode": "deep_analysis"
}
```

### 11.4 Trace

```text
GET /api/v1/traces
GET /api/v1/traces/{trace_id}
GET /api/v1/traces/{trace_id}/events
```

Trace 详情页按 `trace_event.seq` 回放。

---

## 12. SSE 事件模型

SSE 事件和 Trace 事件共用统一 schema。

```json
{
  "trace_id": "trace_xxx",
  "seq": 1,
  "event_type": "query_rewrite",
  "payload": {},
  "created_at": "2026-06-30T10:00:00+08:00"
}
```

第一版事件类型：

| event_type | 用途 |
|------------|------|
| `trace_started` | 创建 Trace |
| `mode_selected` | 记录普通问答或深度分析 |
| `query_rewrite_started` | 开始 query rewrite |
| `query_rewrite_finished` | 输出 rewrite 结果 |
| `retrieval_started` | 开始检索 |
| `retrieval_hit` | 输出单条命中 chunk |
| `retrieval_finished` | 检索结束 |
| `rerank_started` | 开始 rerank |
| `rerank_finished` | 输出 rerank 后结果 |
| `excel_analysis_started` | 开始 Excel 计算 |
| `excel_analysis_finished` | 输出 Excel 计算结果 |
| `prompt_built` | 保存最终 prompt |
| `answer_delta` | 流式回答片段 |
| `citation` | 来源引用 |
| `trace_finished` | Trace 完成 |
| `error` | 错误事件 |

问答页实时展示：

- query rewrite
- 命中文档
- 引用片段
- rerank 结果
- Excel 计算过程
- 回答流
- 源文件引用

Trace 详情页展示：

- 全部事件
- prompt
- 模型响应
- 检索结果
- rerank 分数
- 引用证据
- 错误信息

---

## 13. 普通问答 Workflow

```text
用户提问
-> 创建 trace
-> query rewrite
-> embedding
-> Milvus 检索，按权限等级过滤
-> rerank
-> 构造 prompt
-> LLM 流式生成
-> 输出 citation
-> 保存 chat_message
-> trace 完成
```

普通问答特点：

- 单轮检索。
- 不做反思补检。
- 适用于答案集中在单个或少量文档中的问题。

---

## 14. 深度分析 Workflow

```text
用户提问
-> 创建 trace
-> query rewrite
-> 第一轮检索
-> rerank
-> 判断证据是否足够
-> 必要时追加 query
-> 最多 3 轮
-> 如命中 Excel 且需要计算，调用 pandas CLI
-> 汇总证据
-> LLM 流式生成
-> 输出 citation
-> trace 完成
```

深度分析特点：

- 第一版仍然是单 workflow，不做 multi-agent。
- 节点可以扩展，后续可拆成多个 agent 节点。
- 适用于开放式问题、多文件聚合、Excel 统计分析。

---

## 15. 前端信息架构

### 15.1 登录页

- 用户名密码登录。
- 保存 token。
- 登录后进入问答页。

### 15.2 文档管理页

管理员视图：

- 上传文档。
- 选择文档权限等级。
- 查看解析状态。
- 查看源文件。
- 删除文档。

普通员工视图：

- 只读文档列表。
- 只能看到权限允许的文档。
- 可打开源文件。

### 15.3 问答页

- 左侧：会话列表。
- 中间：问答消息。
- 右侧或消息下方：实时检索链路。
- 输入区：问题输入 + 模式切换。

模式：

- 普通问答
- 深度分析

实时链路显示：

- rewrite query
- 命中文档
- 引用片段
- rerank 分数
- Excel 计算步骤
- 来源引用

### 15.4 Trace 详情页

- 按事件顺序展示完整 Trace。
- 支持查看 prompt、模型响应、检索结果、rerank 结果。
- 支持从问答消息跳转。

---

## 16. 架构图

架构图源文件：

```text
docs/diagrams/rag_prototype_architecture.excalidraw
```

如本地安装 Excalidraw 或打开 excalidraw.com，可直接导入查看。

---

## 17. 第一阶段落地顺序

建议按下面顺序实现，避免同时改太多模块：

1. 新建 DDD 目录骨架与基础实体。
2. 重建认证与角色等级模型。
3. 实现 OSS 文件上传与文档表。
4. 实现异步解析任务表与 worker 雏形。
5. 实现 docx 纯文本解析。
6. 接入多模态 PDF 解析，完成图文 PDF 结构化抽取。
7. 实现 Excel 语义 Markdown 索引。
8. 接入 embedding 服务与 Milvus 写入。
9. 实现普通 RAG + rerank + SSE。
10. 实现 Trace 事件保存与 Trace 详情接口。
11. 实现深度分析 workflow 与 Excel pandas CLI。
12. 重建 4 个前端页面。

---

## 18. 风险与约束

| 风险 | 处理方式 |
|------|----------|
| 多模态解析耗时长 | 必须异步解析，前端展示解析状态 |
| Milvus metadata filter 不稳定 | 优先检索阶段过滤，不行则扩大召回后应用层过滤 |
| Excel 计算不安全 | pandas CLI 必须受控执行，限制文件路径和执行环境 |
| Trace 数据量大 | 第一版全量保存，后续再做保留策略 |
| 现有代码耦合旧 Agent | 原型采用新目录逐步替换，不在旧 Agent 上修补 |
| 模型服务配置混乱 | 先通过 Provider 封装，后续再统一模型配置表 |

---

## 19. 自检清单

- [x] 原型边界与用户确认一致
- [x] 未引入 multi-agent、skill、版本管理等暂缓能力
- [x] 保留后续扩展端口
- [x] 明确了文档解析、检索、问答、Trace 主链路
- [x] 明确了前端 4 页范围
- [x] 明确了核心数据表草图

✅ 功能完成并已自检
