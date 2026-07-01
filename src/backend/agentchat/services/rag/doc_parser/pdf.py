import os
import aiofiles
import pathlib
from urllib.parse import urljoin
from loguru import logger

from agentchat.settings import app_settings
from agentchat.services.storage import storage_client
from agentchat.services.rag.doc_parser.markdown import markdown_parser
from agentchat.services.rag.doc_parser.multimodal_pdf import multimodal_pdf_parser
from agentchat.utils.file_utils import get_object_storage_base_path, get_convert_markdown_images_dir, \
    generate_unique_filename


class PDFParser:

    def __init__(self):
        pass

    async def convert_markdown(self, file_path: str):
        markdown_dir, _ = get_convert_markdown_images_dir()
        logger.info("使用多模态模型解析 PDF 并生成结构化 Markdown")
        md_text_words = await multimodal_pdf_parser.parse_pdf_file_to_markdown(file_path)

        if not md_text_words.strip():
            raise ValueError("PDF 解析未得到有效文本，请检查文档内容或多模态模型配置。")

        markdown_output_path = os.path.join(markdown_dir, generate_unique_filename(file_path, "md"))
        output_markdown_file = pathlib.Path(markdown_output_path)
        output_markdown_file.write_bytes(md_text_words.encode())
        logger.info(f"PDF Convert MarkDown Successful！MarkDown Path: {output_markdown_file}")

        await self.upload_file_to_oss(markdown_output_path)

        return markdown_output_path

    async def parse_into_chunks(self, file_id, file_path, knowledge_id):
        markdown_file = await self.convert_markdown(file_path)
        return await markdown_parser.parse_into_chunks(file_id, markdown_file, knowledge_id)

    async def upload_file_to_oss(self, file_path):
        async with aiofiles.open(file_path, "rb") as file:
            file_content = await file.read()
            oss_object_name = get_object_storage_base_path(os.path.basename(file_path))
            sign_url = urljoin(app_settings.storage.active.base_url, oss_object_name)

            storage_client.sign_url_for_get(sign_url)
            storage_client.upload_file(oss_object_name, file_content)
            return sign_url

pdf_parser = PDFParser()
