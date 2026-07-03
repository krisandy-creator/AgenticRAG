class IndexingWriteError(Exception):
    """索引写入失败，调用方应将文档/job 标记为 failed。"""

    def __init__(
        self,
        backend: str,
        document_id: str,
        chunk_count: int,
        message: str,
    ):
        self.backend = backend
        self.document_id = document_id
        self.chunk_count = chunk_count
        super().__init__(f"[{backend}] document_id={document_id} chunks={chunk_count}: {message}")
