from __future__ import annotations

import json

from pydantic import ValidationError

from luxar.adapters.deepseek.client import JsonCompletionClient
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.ports.errors import CapabilityError


class DeepSeekKnowledgeTaskParser:
    def __init__(self, client: JsonCompletionClient, model: str) -> None:
        self._client = client
        self._model = model

    def parse(self, task_text: str) -> KnowledgeTask:
        schema = KnowledgeTask.model_json_schema()
        payload = self._client.complete_json(
            system_prompt=(
                "你是 LUXAR 内部的知识操作解析组件，不直接与用户对话。"
                "只输出符合 Schema 的 JSON。list/search 是只读；upsert、delete、"
                "import_pdf 会修改知识库。read_pdf 表示只读取、检查或理解 PDF，"
                "不写知识库；import_pdf 表示读取后写入知识库。"
                "用户明确给出绝对 PDF 路径时，必须原样写入 file_path；"
                "用户给出当前项目内路径时写入 relative_path。绝不能修改盘符、"
                "目录、文件名或把绝对路径伪装成相对路径。"
                "缺少执行所必需的信息时写入 missing_fields，不得猜测文档 ID、路径或正文。"
                "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
            ),
            user_prompt=json.dumps({"task_text": task_text}, ensure_ascii=False),
            model=self._model,
        )
        try:
            return KnowledgeTask.model_validate(payload)
        except ValidationError as error:
            raise CapabilityError(
                category="invalid_schema", message="知识操作解析结果无效", retryable=False
            ) from error
