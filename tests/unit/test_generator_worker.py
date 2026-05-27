from unittest.mock import MagicMock
from pathlib import Path

from luxar.agent.workers.generator import GeneratorWorker
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMResponse

def test_generator_worker_success(tmp_path):
    config = AgentConfig()
    worker = GeneratorWorker(config)
    
    mock_llm = MagicMock()
    mock_llm.complete.return_value = LLMResponse(
        content="```c header\n#ifndef FOO_H\n#define FOO_H\n#endif\n```\n\n```c source\n#include \"foo.h\"\n```",
        provider="mock",
        model="mock",
        raw={},
    )
    worker.llm_client = mock_llm
    
    result = worker.generate_files(
        context_data={"chip_name": "FOO123"},
        skill_instructions="Generate a driver",
        output_dir=str(tmp_path),
        stem="foo123",
    )
    
    assert result["success"] is True
    assert "header_path" in result
    assert "source_path" in result
    
    header_path = Path(result["header_path"])
    source_path = Path(result["source_path"])
    
    assert header_path.exists()
    assert source_path.exists()
    assert header_path.read_text(encoding="utf-8") == "#ifndef FOO_H\n#define FOO_H\n#endif\n"
    assert source_path.read_text(encoding="utf-8") == "#include \"foo.h\"\n"
