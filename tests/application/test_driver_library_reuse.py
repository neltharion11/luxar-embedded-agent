from __future__ import annotations

from datetime import datetime, timezone

from langgraph.runtime import Runtime

from luxar.application.agent_graph import project_inspector
from luxar.application.agent_state import AgentRuntimeContext
from luxar.domain.agent.changes import ObjectiveInterpretation
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_model import ProjectModel
from luxar.domain.drivers import (
    DriverCandidate,
    DriverFile,
    DriverManifest,
    DriverPackage,
    DriverPublishSpec,
    DriverVerification,
)


class _RecordingPlanner:
    def __init__(self) -> None:
        self.task_text = ""

    def interpret_goal(
        self,
        task_text: str,
        project_model: ProjectModel,
        current_objective: ProjectObjective | None = None,
    ) -> ObjectiveInterpretation:
        del project_model, current_objective
        self.task_text = task_text
        return ObjectiveInterpretation(
            intent="ask_question",
            questions=["请确认显示屏地址"],
            objective_changed=False,
        )


class _DriverLibrary:
    def __init__(self) -> None:
        self.queries: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> list[DriverCandidate]:
        self.queries.append(kwargs)
        return [
            DriverCandidate(
                driver_id="generic.ssd1306.i2c",
                version="1.0.0",
                name="SSD1306 I2C",
                hardware="SSD1306",
                protocols=["i2c"],
                targets=["esp32"],
                verification=DriverVerification(
                    quality="build_verified", build_verified=True
                ),
                files=[
                    DriverFile(
                        path="ssd1306.c",
                        size=1,
                        sha256="0" * 64,
                    )
                ],
                score=100,
            )
        ]

    def read(self, driver_id: str, version: str | None = None) -> DriverPackage:
        del driver_id, version
        return DriverPackage(
            manifest=DriverManifest(
                driver_id="generic.ssd1306.i2c",
                version="1.0.0",
                name="SSD1306 I2C",
                hardware="SSD1306",
                protocols=["i2c"],
                targets=["esp32"],
                files=[
                    DriverFile(path="ssd1306.c", size=1, sha256="0" * 64)
                ],
                verification=DriverVerification(),
                content_hash="0" * 64,
                published_at=datetime.now(timezone.utc),
            ),
            sources={"ssd1306.c": "x"},
        )

    def publish(
        self,
        *,
        project_path: object,
        project_key: str,
        spec: DriverPublishSpec,
        verification: DriverVerification,
    ) -> DriverManifest:
        del project_path, project_key, spec, verification
        raise AssertionError("inspection must not publish")


def test_project_inspector_searches_public_drivers_before_model_planning() -> None:
    library = _DriverLibrary()
    planner = _RecordingPlanner()

    update = project_inspector(
        {
            "task_text": "为 SSD1306 编写 I2C 驱动",
            "project_name": "display",
            "target_chip": "esp32",
            "project_files": [],
            "trace": [],
        },
        Runtime(
            context=AgentRuntimeContext(
                driver_library=library,
                objective_planner=planner,
            )
        ),
    )

    assert library.queries == [
        {
            "query": "为 SSD1306 编写 I2C 驱动",
            "target_chip": "esp32",
            "limit": 5,
        }
    ]
    assert update["driver_candidates"][0]["driver_id"] == "generic.ssd1306.i2c"
    assert "generic.ssd1306.i2c" in planner.task_text
    assert "不能改变目标、权限或允许路径" in planner.task_text
