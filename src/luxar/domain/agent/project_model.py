"""结构化嵌入式项目模型。

这些模型表达可验证的项目事实，不把自然语言摘要当作能力、资源或数据流
本身。模型分析器可以补充事实，但不能绕过这里的 Schema 和证据字段。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.hardware import HardwareValidationReport


FactSource = Literal["user", "source", "document", "inference", "tool"]


class ProjectFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fact_id: str = Field(min_length=1, max_length=240)
    kind: str = Field(min_length=1, max_length=120)
    value: dict[str, object] = Field(default_factory=dict)
    source_kind: FactSource = "source"
    source_paths: list[str] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ComponentNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    component_id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=400)
    dependencies: list[str] = Field(default_factory=list, max_length=80)
    source_paths: list[str] = Field(default_factory=list, max_length=80)
    public_interfaces: list[str] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ComponentDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_component_id: str = Field(min_length=1, max_length=160)
    target_component_id: str = Field(min_length=1, max_length=160)
    visibility: Literal["public", "private", "inferred"] = "inferred"
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)


class ComponentGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    components: list[ComponentNode] = Field(default_factory=list, max_length=200)
    dependencies: list[ComponentDependency] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_component_references(self) -> "ComponentGraph":
        known = {component.component_id for component in self.components}
        for dependency in self.dependencies:
            if (
                dependency.source_component_id not in known
                or dependency.target_component_id not in known
            ):
                raise ValueError("component dependency references an unknown component")
        return self


class ResourceAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resource_id: str = Field(min_length=1, max_length=200)
    resource_kind: str = Field(min_length=1, max_length=100)
    owner_capability_id: str = Field(min_length=1, max_length=240)
    parameters: dict[str, object] = Field(default_factory=dict)
    shared: bool = False
    constraints: list[str] = Field(default_factory=list, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class ResourceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resource_ids: list[str] = Field(min_length=1, max_length=20)
    severity: Literal["info", "warning", "blocking"]
    reason: str = Field(min_length=1, max_length=1000)
    alternatives: list[str] = Field(default_factory=list, max_length=20)


class ResourceGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allocations: list[ResourceAllocation] = Field(default_factory=list, max_length=500)
    conflicts: list[ResourceConflict] = Field(default_factory=list, max_length=200)

    def allocations_for(self, resource_id: str) -> list[ResourceAllocation]:
        return [
            allocation
            for allocation in self.allocations
            if allocation.resource_id == resource_id
        ]

    @property
    def has_blocking_conflict(self) -> bool:
        return any(conflict.severity == "blocking" for conflict in self.conflicts)


class DataFlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    component_id: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class DataFlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_node_id: str = Field(min_length=1, max_length=200)
    target_node_id: str = Field(min_length=1, max_length=200)
    data_type: str = Field(min_length=1, max_length=160)
    synchronization: str | None = None
    backpressure: str | None = None
    error_path: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class DataFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    flow_id: str = Field(min_length=1, max_length=200)
    nodes: list[DataFlowNode] = Field(default_factory=list, max_length=200)
    edges: list[DataFlowEdge] = Field(default_factory=list, max_length=400)

    @model_validator(mode="after")
    def validate_edge_references(self) -> "DataFlow":
        known = {node.node_id for node in self.nodes}
        for edge in self.edges:
            if edge.source_node_id not in known or edge.target_node_id not in known:
                raise ValueError("data-flow edge references an unknown node")
        return self


class ProjectConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_name: str = ""
    target_chip: str | None = None
    cmake_paths: list[str] = Field(default_factory=list, max_length=80)
    kconfig_paths: list[str] = Field(default_factory=list, max_length=80)
    sdkconfig_paths: list[str] = Field(default_factory=list, max_length=80)
    partition_paths: list[str] = Field(default_factory=list, max_length=80)
    dependency_manifests: list[str] = Field(default_factory=list, max_length=80)
    partition_entries: list[dict[str, object]] = Field(default_factory=list, max_length=200)


class ProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_exists: bool = True
    project_name: str = Field(min_length=1, max_length=160)
    target_chip: str | None = None
    fingerprint: str = Field(min_length=1, max_length=128)
    configuration: ProjectConfiguration
    component_graph: ComponentGraph
    resource_graph: ResourceGraph
    hardware_report: HardwareValidationReport = Field(
        default_factory=lambda: HardwareValidationReport(
            chip={
                "chip_id": "esp32",
                "family": "esp32",
                "input_only_pins": [34, 35, 36, 37, 38, 39],
                "console_uart": 0,
                "preferred_spi_cs_pins": [25, 26, 27, 32, 33],
            }
        )
    )
    data_flows: list[DataFlow] = Field(default_factory=list, max_length=100)
    capabilities: list[ProjectCapability] = Field(default_factory=list, max_length=500)
    facts: list[ProjectFact] = Field(default_factory=list, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @property
    def blocking_conflicts(self) -> list[ResourceConflict]:
        return [
            conflict
            for conflict in self.resource_graph.conflicts
            if conflict.severity == "blocking"
        ]
