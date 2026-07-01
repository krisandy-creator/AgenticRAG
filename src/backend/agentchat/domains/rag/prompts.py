from typing import Any

from agentchat.domains.indexing.entities import SearchHit


def build_rag_prompt(
    question: str,
    hits: list[SearchHit],
    mode: str,
    excel_result: str | None = None,
    structured_intent: dict[str, Any] | None = None,
) -> str:
    intent = structured_intent or {}
    evidence = "\n\n".join(
        f"[{index + 1}] {hit.file_name} 页码:{hit.page_no or '-'}\n{hit.content}"
        for index, hit in enumerate(hits)
    )
    question_type = intent.get("question_type") or "fact"
    if mode == "deep_analysis":
        analysis_hint = "请进行跨文档归纳，最多 3 条要点，说明结论边界和不确定性。"
    elif question_type == "enumeration":
        analysis_hint = (
            "用户询问条目、标准或要求清单。请基于证据分条列出，每条一行，"
            "最多 8 条，保留关键数值和条件，不要大段粘贴原文。"
        )
    else:
        analysis_hint = "请基于证据直接回答，默认 1 句话，最多 2 句话。"
    excel_hint = f"\nExcel 计算结果:\n{excel_result}" if excel_result else ""
    return (
        "你是企业内部知识库助手，只能根据给定证据回答。\n"
        "先判断哪些证据与问题直接相关，再提炼答案结论。\n"
        "短问短答：用户询问可从证据字段直接抽取的信息时，优先输出字段值和单位。\n"
        "不要逐条复述检索片段，不要把原文大段粘贴到回答正文里。\n"
        "不要输出“根据当前企业知识库检索结果”“最相关的信息集中在”“核心结论是”“具体来源已整理”等套话。\n"
        "不要在正文里描述来源数量、来源位置或提示用户打开来源链接，来源会由系统在回答下方单独展示。\n"
        "回答正文只输出结论、必要依据和行动建议；来源链接会由系统在回答下方单独展示。\n"
        "如果证据不足，请明确说明缺少哪些信息。\n"
        f"问题：{question}\n"
        f"模式：{mode}\n"
        f"问题类型：{question_type}\n"
        f"{analysis_hint}\n"
        f"证据片段（仅供判断，不要原样复述）：\n{evidence or '未检索到可用证据'}"
        f"{excel_hint}\n"
    )
