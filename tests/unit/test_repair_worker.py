from unittest.mock import MagicMock, patch
from pathlib import Path
import json

from luxar.agent.workers.repair import RepairWorker
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMResponse

def test_repair_file_success(tmp_path):
    config = AgentConfig()
    worker = RepairWorker(config)
    
    target_file = tmp_path / "main.c"
    target_file.write_text("int main() { return 0; }", encoding="utf-8")
    
    report_json = json.dumps({"passed": False, "issues": []})
    
    mock_llm = MagicMock()
    mock_llm.complete.return_value = LLMResponse(
        content="```c\nint main() {\n    return 1;\n}\n```",
        provider="mock",
        model="mock",
        raw={},
    )
    worker.llm_client = mock_llm
    
    result = worker.repair_file(
        file_path=str(target_file),
        context_report=report_json,
        skill_instructions="Make it return 1 instead of 0",
        apply_changes=True,
    )
    
    assert result["success"] is True
    assert result["applied"] is True
    assert target_file.read_text(encoding="utf-8") == "int main() {\n    return 1;\n}\n"
