"""受限代码变更执行器 Port。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleValidation


class CodeExecutorPort(Protocol):
    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation: ...
