from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from luxar.application.agent_persistence import save_agent_snapshot
from luxar.application.workbench_persistence import knowledge_workbench_snapshot
from luxar.database import (
    PendingApprovalRecord,
    SQLitePersistence,
    TransientPersistence,
)
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.failures import AgentFailureRecord
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.tasks import AgentTaskGraph, build_task_graph
from luxar.domain.agent.verification import VerificationPlan
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.web import create_app


def _make_project(root: Path) -> None:
    project = root / "blink"
    project.mkdir(exist_ok=True)
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        "project(blink)\n",
        encoding="utf-8",
    )


def _save_completed_agent_state(persistence: object) -> None:
    objective = ProjectObjective(
        objective_id="agent-api-objective",
        title="新增 GPIO33",
        description="保留 GPIO13 并新增 GPIO33 高电平输出",
        acceptance_criteria=["GPIO33 输出高电平"],
        revision=2,
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="add",
                capability_id="gpio.output:P33",
                desired_state={"pin": 33, "level": 1},
                rationale="新增状态指示灯",
            )
        ]
    )
    graph = build_task_graph(
        objective,
        change_set,
        allowed_paths_by_capability={"gpio.output:P33": ["F:/secret/main.c"]},
        verification_plan=VerificationPlan(require_build=False),
    )
    for task in graph.tasks:
        graph = graph.update_task(task.task_id, status="passed", attempts=1)
    evidence_ids = [
        f"task:{task.task_id}"
        for task in graph.tasks
        if task.kind in {"code_change", "verify_acceptance"}
    ]
    criteria = [
        criterion.model_copy(
            update={
                "status": "passed",
                "evidence_ids": list(criterion.required_evidence),
            }
        )
        for criterion in graph.acceptance_criteria
    ]
    graph = graph.model_copy(update={"acceptance_criteria": criteria})
    state = {
        "source_message_id": "firmware-agent-thread",
        "objective": objective,
        "change_set": change_set,
        "capabilities": [
            ProjectCapability(
                capability_id="gpio.output:P33",
                kind="gpio.output",
                parameters={"pin": 33, "level": 1},
                status="verified",
                evidence_ids=evidence_ids,
                source_paths=["F:/secret/main.c"],
            )
        ],
        "task_graph": graph,
        "acceptance_criteria": criteria,
        "evidence_ids": evidence_ids,
        "current_task_id": graph.tasks[-1].task_id,
        "acceptance_passed": True,
        "build_verified": False,
        "hardware_function_verified": False,
        "failure_history": [
            AgentFailureRecord(
                task_id=graph.tasks[-1].task_id,
                category="semantic",
                signature="recovered-source-error",
                message="首次源码断言未通过，修复后重试成功",
                attempt=1,
            )
        ],
        "status": "completed",
        "trace": ["load_project_session", "supervisor", "complete_objective"],
    }
    save_agent_snapshot(persistence, "0:blink", state)  # type: ignore[arg-type]
    persistence.append_agent_interaction(  # type: ignore[attr-defined]
        interaction_id="interaction-initial",
        project_key="0:blink",
        objective_id=objective.objective_id,
        kind="change_objective",
        payload={"message": "新增 GPIO33"},
    )


def test_agent_snapshot_and_subresource_apis_are_path_safe(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    _save_completed_agent_state(persistence)
    client = TestClient(create_app(projects_roots=[tmp_path], persistence=persistence))

    snapshot = client.get("/api/projects/blink/agent")

    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["status"] == "completed"
    assert payload["workflow_family"] == "supervisor_firmware"
    assert payload["task_mode"] == "firmware"
    assert payload["thread_id"] == "firmware-agent-thread"
    assert payload["objective"]["revision"] == 2
    assert payload["tasks"][-1]["status"] == "passed"
    assert payload["capabilities"][0]["status"] == "verified"
    assert payload["acceptance_passed"] is True
    assert payload["evidence"][0]["accepted_by"]
    assert payload["recovery"][0]["attempt"] == 1
    assert payload["trace"] == [
        "load_project_session",
        "supervisor",
        "complete_objective",
    ]
    assert "F:/secret" not in snapshot.text
    assert client.get("/api/projects/blink/agent/objective").status_code == 200
    assert len(client.get("/api/projects/blink/agent/tasks").json()) == 4
    assert len(client.get("/api/projects/blink/agent/capabilities").json()) == 1
    assert len(client.get("/api/projects/blink/agent/evidence").json()) == 2


def test_agent_interaction_kinds_do_not_mutate_objective_directly(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    _save_completed_agent_state(persistence)
    client = TestClient(create_app(projects_roots=[tmp_path], persistence=persistence))

    question = client.post(
        "/api/projects/blink/agent/interactions",
        json={"kind": "question", "message": "GPIO 输出模式是什么？"},
    )
    plan_change = client.post(
        "/api/projects/blink/agent/interactions",
        json={
            "kind": "change_plan",
            "message": "先完成 Host 测试",
            "target_id": "agent-api-objective:verify",
        },
    )

    assert question.status_code == 202
    assert question.json()["queued"] is True
    assert plan_change.status_code == 202
    objective = client.get("/api/projects/blink/agent/objective").json()
    assert objective["revision"] == 2
    interactions = client.get("/api/projects/blink/agent/interactions").json()
    assert [item["kind"] for item in interactions] == [
        "change_objective",
        "question",
        "change_plan",
    ]


def test_agent_snapshot_survives_sqlite_reopen_and_web_restart(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_project(projects)
    database = tmp_path / "agent-api.sqlite3"
    first = SQLitePersistence(database)
    _save_completed_agent_state(first)

    reopened = SQLitePersistence(database)
    with TestClient(
        create_app(projects_roots=[projects], persistence=reopened)
    ) as client:
        response = client.get("/api/projects/blink/agent")

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 2
    assert len(payload["tasks"]) == 4
    assert len(payload["evidence"]) == 2


def test_agent_api_returns_404_before_agent_state_exists(tmp_path: Path) -> None:
    _make_project(tmp_path)
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=TransientPersistence())
    )

    response = client.get("/api/projects/blink/agent")

    assert response.status_code == 404


def test_agent_workspace_displays_persisted_knowledge_task(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    thread_id = "knowledge-workbench"
    state = {
        "task_text": "检索 ESP-IDF GPIO 文档",
        "task_mode": "knowledge",
        "knowledge_task": KnowledgeTask(
            action="search",
            summary="检索 GPIO 文档",
            query="gpio_set_level",
        ),
        "knowledge_result": {
            "matches": [
                {"title": "GPIO API", "content": "bounded in workbench"}
            ]
        },
        "status": "completed",
        "trace": [
            "analyze_knowledge_task",
            "review_knowledge_task",
            "execute_knowledge_task",
            "completed",
        ],
    }
    persistence.save_workbench_snapshot(
        project_key="0:blink",
        workflow_family="knowledge_task",
        thread_id=thread_id,
        snapshot=knowledge_workbench_snapshot(  # type: ignore[arg-type]
            state,
            thread_id=thread_id,
        ),
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.get("/api/projects/blink/agent")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_family"] == "knowledge_task"
    assert payload["task_mode"] == "knowledge"
    assert payload["status"] == "completed"
    assert payload["supports_interactions"] is False
    assert payload["tasks"][-1]["status"] == "passed"
    assert payload["knowledge_task"]["query"] == "gpio_set_level"
    assert payload["knowledge_result"] == {
        "match_count": 1,
        "match_titles": ["GPIO API"],
    }
    assert "bounded in workbench" not in response.text
    interaction = client.post(
        "/api/projects/blink/agent/interactions",
        json={"kind": "question", "message": "继续"},
    )
    assert interaction.status_code == 409


def test_agent_workspace_recovers_pre_fix_pending_knowledge_task(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    task = KnowledgeTask(
        action="upsert",
        summary="保存 OLED 笔记",
        source_uri="user://note",
        title="OLED",
        content="content must not enter the workbench snapshot",
    )
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="old-pending-knowledge",
            request={
                "kind": "knowledge_write",
                "title": "确认知识库变更",
                "summary": task.summary,
                "questions": [],
                "options": ["批准执行", "取消任务"],
                "operation": task.model_dump(mode="json"),
                "allow_feedback": True,
            },
            runtime_config={
                "workflow_family": "knowledge_task",
                "task_text": "保存 OLED 笔记",
            },
        )
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.get("/api/projects/blink/agent")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "awaiting_user"
    assert payload["current_task_id"] == "review_knowledge_task"
    assert payload["tasks"][1]["requires_approval"] is True
    assert payload["knowledge_task"]["action"] == "upsert"
    assert "content must not enter" not in response.text
