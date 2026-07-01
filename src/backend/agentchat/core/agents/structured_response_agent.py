import json
import re
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage

from agentchat.core.callbacks import usage_metadata_callback
from agentchat.core.models.manager import ModelManager


class StructuredResponseAgent:
    def __init__(self, response_format):
        self.response_format = response_format
        self.model = ModelManager.get_conversation_model()

    def get_structured_response(self, messages):
        prompt = messages if isinstance(messages, str) else json.dumps(messages, ensure_ascii=False)

        model_messages = [
            SystemMessage(content="你是一个只输出单个JSON对象的助手。不要输出Markdown，不要输出数组/列表，不要输出额外文字。只输出一个单独的JSON对象。"),
            HumanMessage(content=prompt),
        ]

        result = self.model.invoke(
            model_messages,
            config={"callbacks": [usage_metadata_callback]},
        )
        content = (getattr(result, "content", None) or "").strip()

        # 尝试直接解析
        try:
            return self.response_format.model_validate_json(content)
        except Exception as e:
            # 打印第一次解析失败的错误
            print(f"第一次解析失败: {type(e).__name__}: {e}")

            # 尝试提取单个 JSON 对象
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                try:
                    return self.response_format.model_validate_json(match.group(0))
                except Exception as e2:
                    # 打印第二次解析失败的错误
                    print(f"第二次解析失败: {type(e2).__name__}: {e2}")

            # 尝试处理返回数组的情况 - 取第一个元素
            array_match = re.search(r"\[[\s\S]*\]", content)
            if array_match:
                try:
                    array_data = json.loads(array_match.group(0))
                    if isinstance(array_data, list) and len(array_data) > 0:
                        # 如果是数组，取第一个元素并尝试解析
                        first_item = array_data[0] if isinstance(array_data[0], dict) else array_data[0]
                        if isinstance(first_item, dict):
                            return self.response_format.model_validate(first_item)
                except Exception as e3:
                    print(f"数组解析失败: {type(e3).__name__}: {e3}")

            raise ValueError(f"Invalid structured JSON output: {content}")
