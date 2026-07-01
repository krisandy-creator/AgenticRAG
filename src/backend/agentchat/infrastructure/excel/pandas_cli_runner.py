class PandasCliRunner:
    """受控 pandas CLI 占位实现，避免第一版执行任意代码。"""

    async def analyze(self, question: str, document_ids: list[str]) -> str:
        try:
            import pandas  # noqa: F401
        except Exception:
            return "当前环境未安装 pandas，已记录需要 Excel 计算，但第一版仅返回语义索引证据。"
        return f"已识别到需要对 {len(document_ids)} 个 Excel 文档做受控计算，问题：{question}"

