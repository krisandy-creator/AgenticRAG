# 事实型 QA 检索失败案例解决方案

> 关联分析：`docs/fail-case.md`、`docs/fail-case01.md`
> 状态：待实施
> 日期：2026-07-03

---

## 1. 问题定性（已验证）

### 1.1 失败案例

- 问题："在泡沫切断规则中每次切断后还要继续推多少距离"（及变体"还要再往前多送多少距离"）
- 正确证据：包含 "继续推动80mm" 的 chunk
- 现象：LLM 回答"证据不足"

### 1.2 已验证的故障链

| 阶段 | 状态 | 证据 |
|------|------|------|
| 混合检索（BM25 + 向量 + RRF） | 正常 | 正确 chunk 在候选池中 |
| Rerank API | **正常** | 实测正确 chunk 得分 0.938944，排第 1（见 fail-case01.md） |
| `EvidenceSelector._rank_by_intent` | **故障点** | `combined = 0.45 × 0.939 + 0.55 × 0.032 ≈ 0.44`，rerank 信号被词法分稀释 |
| `_mmr_select` top_k=6 | 放大器 | 分数被拖低后可能被挤出 top 6 |
| LLM 回答 | 放大器 | prompt 允许"证据不足"，证据缺失时直接触发 |

### 1.3 根因

`_intent_score`（`evidence_selection.py:130-170`）用**字符 n-gram 词法重叠**度量"chunk 与问题的相关性"。
事实型 QA 的问句（"多少距离"）与答案（"80mm"）词法天然正交，该分数对正确证据恒接近 0。
而 cross-encoder rerank 模型的职责恰恰就是弥合这种问答词法鸿沟——**当前流水线用一个更弱的词法信号
以 55% 的权重推翻了 rerank 的判断**，这是设计矛盾，不是权重调参问题。

次生问题：

1. **量纲混乱**：RRF 分数学上限 ≈ 0.033（`1/(60+1) × 2`），rerank 分 0~1，intent 分 0~1，
   三者被同一线性公式混合。rerank 失败走 fallback 时，`0.45 × RRF` 贡献不足 0.015，
   intent 分实际占比 >97%。
2. **意图重排执行两次**：rerank 失败时 `RerankProvider`（`rerank_provider.py:66`）先调一次
   `_rank_by_intent`（原地改写 `hit.score`），`workflow.py:99` 的 `EvidenceSelector.select`
   再调一次，意图权重叠加到 ~80%。
3. **`_rank_by_intent` 有副作用**：原地覆盖 `hit.score`，流水线不幂等。
4. **prompt 兜底缺失**：未告知模型"证据中的具体数值即是对'多少'类问题的直接回答"。

---

## 2. 解决方案

### P0：rerank 成功时，词法分退出排序（治本，必须做）

**原则：rerank 分是排序的唯一权威信号；`EvidenceSelector` 只做 section 聚合、MMR 去冗余和截断，不再重排序。**

改动点：

1. `workflow.py`：把 rerank 结果的 provider 传给 selector。

```python
reranked_hits, evidence_debug = self.evidence_selector.select(
    question,
    reranked_pool,
    expansion.structured_intent,
    rerank_top_k,
    rerank_provider=self.reranker.last_provider,   # 新增
)
```

2. `evidence_selection.py`：`select` 按 provider 分流。

```python
def select(self, question, hits, structured_intent, top_k, *, rerank_provider=""):
    ...
    aggregated = self._aggregate_by_section(hits)
    if rerank_provider == "configured_rerank":
        # rerank 分权威，词法分仅作小额 tie-break，不改变量级
        ranked = self._rank_with_tiebreak(question, aggregated, intent)   # 0.9 / 0.1
    else:
        ranked = self._rank_by_intent(question, aggregated, intent)       # fallback 路径，见 P1
    selected = self._mmr_select(ranked, effective_k)
```

`_rank_with_tiebreak` 中权重取 `0.9 × rerank + 0.1 × intent`（或更极端：完全不混合，
intent 仅在 rerank 分差 < 0.02 时作同分裁决）。验证本 case：
`0.9 × 0.939 + 0.1 × 0.032 = 0.848`，正确 chunk 稳居第一。

3. 顺带修副作用：`_rank_by_intent` / `_rank_with_tiebreak` 不再覆盖 `hit.score`，
   排序分写入 `score_breakdown["combined_select"]`，`hit.score` 保留 rerank 原始值
   （MMR 的 relevance 改用排序分）。

### P1：修复 fallback 路径（rerank API 失败时）

1. **消除二次重排**：`RerankProvider` 的 fallback 分支不再调用 `_rank_by_intent`，
   只按 RRF 原始分排序返回，并置 `last_provider = "fallback_rrf"`；
   意图重排统一收敛到 `EvidenceSelector` 一处执行。
2. **改用基于排名的融合**：RRF 分（≤0.033）与 intent 分（0~1）不可线性混合。
   fallback 时对"RRF 排名"和"intent 排名"再做一次 RRF 融合：

```python
combined_rank_score = 1 / (60 + rrf_rank) + 1 / (60 + intent_rank)
```

   这样两路信号天然同量纲，权重语义清晰。

### P2：为事实型问题增加"可答性"信号（方向翻转）

`_intent_score` 的哲学是"chunk 与问题越像越相关"，对事实型问题应翻转为
"chunk 包含问题所要的答案类型即相关"：

```python
ANSWER_PATTERNS = {
    "quantity": r"\d+(?:\.\d+)?\s*(?:mm|cm|m|毫米|厘米|米|%|度|个|次|条|kg|g)",
    "time":     r"\d+\s*(?:天|日|周|月|年|小时|分钟|秒)",
}

def _answerability_bonus(cls, question: str, intent: dict, target_text: str) -> float:
    if intent.get("question_type") != "fact":
        return 0.0
    if re.search(r"(多少|几|多长|多久|多远|何时|什么时候)", question):
        for pattern in cls.ANSWER_PATTERNS.values():
            if re.search(pattern, target_text, re.I):
                return 0.15
    return 0.0
```

加入 `_intent_score` 的返回值（仍受 `min(…, 1.0)` 约束）。
该信号主要服务于 fallback 路径；rerank 成功时按 P0 只占 tie-break 权重，无害。

### P3：MMR 参数微调（低风险）

- P0 落地后 MMR 的 relevance 即为 rerank 分，主要问题已解除。
- `_similarity` 中"同文档同页即 0.85"过于激进：切断规则的多个步骤 chunk 常在同一页，
  会把正确 chunk 挤出去。建议：同页时仍计算内容 n-gram 相似度，取
  `max(0.5, content_sim)`，而非固定 0.85。

---

## 3. 不做什么

- **不改进 n-gram 分词粒度**：词法正交是本质问题，优化分词无法弥合问答鸿沟。
- **不调 0.45/0.55 权重**：权重只是暴露缺陷的旋钮，P0 从结构上移除该混合。
- **不动检索与 RRF 融合**：已验证正常。

---

## 4. 验证方案

### 4.1 回归用例（必须全绿）

| # | 问题 | 期望 |
|---|------|------|
| 1 | 在泡沫切断规则中每次切断后还要继续推多少距离 | top-1 为含 80mm 的 chunk；回答含 "80mm" |
| 2 | 还要再往前多送多少距离 | 含 80mm 的 chunk 进 top 6；回答含 "80mm" |
| 3 | 泡沫与护角之间的间隙是多少 | 回答含 "50~75mm" |
| 4 | （任选一条既有正常通过的枚举型问题） | 不回退 |

### 4.2 单元测试

- `EvidenceSelector.select`：构造 rerank 分 0.94 / intent 分 0.03 的 hit 与
  rerank 分 0.50 / intent 分 0.35 的 hit，断言 `rerank_provider="configured_rerank"` 时前者排第一。
- fallback 路径：断言 `_rank_by_intent` 只执行一次（`score_breakdown` 中
  `intent_select` 键不被覆盖两次）。
- `_answerability_bonus`：含 "80mm" 的 chunk 对"多少距离"问题得到加分。

### 4.3 Trace 验收

- `evidence_selection` 事件的 `score_breakdown` 中保留 `rerank` 原始分，
  新增 `rank_path`（`rerank_authoritative` / `fallback_rrf_fusion`）便于事后归因。

---

## 5. 实施顺序与风险

| 优先级 | 改动 | 文件 | 风险 |
|--------|------|------|------|
| P0 | rerank 权威化 + 去副作用 | `evidence_selection.py`、`workflow.py` | 低；仅改排序逻辑，接口不变 |
| P1 | fallback 单次重排 + 排名融合 | `rerank_provider.py`、`evidence_selection.py` | 低；仅影响 rerank 故障场景 |
| P2 | 可答性加分 | `evidence_selection.py` | 低；加分幅度 0.15 有上限约束 |
| P3 | MMR 同页相似度 | `evidence_selection.py` | 中；需回归枚举型问题防止冗余上升 |

