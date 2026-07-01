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
    if "multi_models" in data:
        data["multi_models"] = MultiModels(**data["multi_models"])

    if "tools" in data:
        data["tools"] = Tools(**data["tools"])

    if "rag" in data:
        data["rag"] = Rag(**data["rag"])

    if "storage" in data:
        data["storage"] = StorageConfig(**data["storage"])

    return data


async def initialize_app_settings(file_path: str = None):
    global app_settings

    # 配置文件路径（按优先级排序）
    local_config_path = "agentchat/config.local.yaml"
    default_config_path = file_path or "agentchat/config.yaml"

    # 优先加载本地配置
    config_data = _load_yaml_file(local_config_path)

    if config_data:
        logger.info(f"✅ 使用本地配置文件: {local_config_path}")
    else:
        # 本地配置不存在，加载默认配置
        config_data = _load_yaml_file(default_config_path)
        if config_data:
            logger.info(f"✅ 使用默认配置文件: {default_config_path}")
        else:
            logger.error("❌ 配置文件加载失败，无法启动应用")
            return

    # 处理配置数据（转换为 Pydantic 模型）
    final_config = _process_config_data(config_data)

    # 应用配置到全局设置
    for key, value in final_config.items():
        setattr(app_settings, key, value)
