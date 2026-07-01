import yaml
from typing import Literal, Optional
from loguru import logger
from types import SimpleNamespace
from pydantic.v1 import BaseSettings, Field

from agentchat.schema.common import MultiModels, ModelConfig, Tools, Rag, StorageConfig


class Settings(BaseSettings):
    redis: dict = {}
    mysql: dict = {}
    server: dict = {}
    langfuse: dict = {}
    whitelist_paths: list = []
    wechat_config: dict = {}
    default_config: dict = {}

    rag: Optional[Rag] = None
    tools: Optional[Tools] = None
    storage: Optional[StorageConfig] = None
    multi_models: Optional[MultiModels] = None


app_settings = Settings()

def _load_yaml_file(file_path: str) -> dict:
    """加载 YAML 文件并返回解析后的数据"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if data is None:
                logger.warning(f"配置文件 {file_path} 解析为空，使用空配置")
                return {}
            logger.info(f"成功加载配置文件: {file_path}")
            return data
    except FileNotFoundError:
        logger.debug(f"配置文件不存在: {file_path}")
        return {}
    except Exception as e:
        logger.error(f"加载配置文件 {file_path} 失败: {e}")
        return {}


def _process_config_data(data: dict) -> dict:
    """处理配置数据，将特定字段转换为 Pydantic 模型"""
    data = dict(data)
    rag_section = data.get("rag") or {}
    if not isinstance(rag_section, dict):
        rag_section = rag_section.dict() if hasattr(rag_section, "dict") else rag_section.model_dump()

    for nested_key in ("multimodal_parse", "parse_worker"):
        if nested_key in data:
            rag_section.setdefault(nested_key, data.pop(nested_key))

    if rag_section:
        data["rag"] = rag_section

    if "multi_models" in data:
        data["multi_models"] = MultiModels(**data["multi_models"])

    if "tools" in data:
        data["tools"] = Tools(**data["tools"])

    if "rag" in data:
        data["rag"] = Rag(**data["rag"])

    if "storage" in data:
        data["storage"] = StorageConfig(**data["storage"])

    return data


def load_app_settings_sync(file_path: str = None) -> None:
    """同步加载 YAML 配置（Worker 等无 FastAPI lifespan 的进程需在 import database 前调用）。"""
    global app_settings

    local_config_path = "agentchat/config.local.yaml"
    default_config_path = file_path or "agentchat/config.yaml"

    config_data = _load_yaml_file(local_config_path)
    if config_data:
        logger.info(f"✅ 使用本地配置文件: {local_config_path}")
    else:
        config_data = _load_yaml_file(default_config_path)
        if config_data:
            logger.info(f"✅ 使用默认配置文件: {default_config_path}")
        else:
            logger.error("❌ 配置文件加载失败，无法启动应用")
            return

    final_config = _process_config_data(config_data)
    for key, value in final_config.items():
        setattr(app_settings, key, value)


async def initialize_app_settings(file_path: str = None):
    load_app_settings_sync(file_path)
