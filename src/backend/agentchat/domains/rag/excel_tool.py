from agentchat.infrastructure.excel.pandas_cli_runner import PandasCliRunner


class ExcelAnalysisTool:
    """深度分析中的 Excel 计算入口。"""

    SPREADSHEET_TYPES = {"excel", "xlsx", "xls", "csv"}

    def __init__(self):
        self.runner = PandasCliRunner()

    def should_run(self, question: str, file_types: list[str]) -> bool:
        del question
        normalized_types = {file_type.lower().lstrip(".") for file_type in file_types if file_type}
        return bool(normalized_types.intersection(self.SPREADSHEET_TYPES))

    async def run(self, question: str, document_ids: list[str]) -> str:
        return await self.runner.analyze(question=question, document_ids=document_ids)
