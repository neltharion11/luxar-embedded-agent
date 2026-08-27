"""无需模型即可运行的 ESP-IDF 结构化项目模型提取器。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from luxar.domain.agent.capabilities import (
    ProjectCapability,
    ProjectCapabilityExtractor,
)
from luxar.domain.agent.project_model import (
    ComponentDependency,
    ComponentGraph,
    ComponentNode,
    DataFlow,
    DataFlowEdge,
    DataFlowNode,
    ProjectConfiguration,
    ProjectFact,
    ProjectModel,
    ResourceAllocation,
    ResourceConflict,
    ResourceGraph,
)
from luxar.domain.agent.hardware import HardwareRuleEngine
from luxar.domain.repairs import ProjectFile


_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp", ".s")
_CMAKE_KEYWORDS = (
    "SRCS",
    "INCLUDE_DIRS",
    "REQUIRES",
    "PRIV_REQUIRES",
    "EMBED_FILES",
    "EMBED_TXTFILES",
    "WHOLE_ARCHIVE",
)
_GPIO_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:GPIO(?:_NUM_)?\s*|P\s*)(\d+)",
    re.IGNORECASE,
)
_COMPONENT_REGISTER_RE = re.compile(
    r"idf_component_register\s*\((?P<body>.*?)\)",
    re.IGNORECASE | re.DOTALL,
)
_PROJECT_RE = re.compile(r"\bproject\s*\(\s*([A-Za-z0-9_.-]+)", re.IGNORECASE)
_TARGET_RE = re.compile(r"CONFIG_IDF_TARGET\s*=\s*[\"]?([A-Za-z0-9_-]+)", re.IGNORECASE)
_TASK_RE = re.compile(
    r"xTaskCreate(?:PinnedToCore)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_PARTITION_RE = re.compile(
    r"^\s*([^,#]+)\s*,\s*([^,#]+)\s*,\s*([^,#]+)\s*,\s*([^,#]*)\s*,\s*([^,#]+)",
)


def _fingerprint(files: Sequence[ProjectFile]) -> str:
    digest = hashlib.sha256(b"luxar-project-model-v1\0")
    for item in sorted(files, key=lambda file: file.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _component_id(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    if parent == ".":
        return "root"
    parts = PurePosixPath(parent).parts
    if parts[0] in {"components", "managed_components"} and len(parts) > 1:
        return "/".join(parts[1:])
    return parent


def _tokens_after_keyword(body: str, keyword: str) -> list[str]:
    stop = "|".join(re.escape(item) for item in _CMAKE_KEYWORDS if item != keyword)
    match = re.search(
        rf"\b{re.escape(keyword)}\b\s+(.*?)(?=\b(?:{stop})\b|$)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return []
    return [token for token in re.split(r"\s+", match.group(1).strip()) if token]


def _owner_for_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if not parts:
        return "project"
    if parts[0] == "components" and len(parts) > 1:
        return f"components/{parts[1]}"
    if parts[0] == "main":
        return "main"
    return parts[0]


class ProjectModelExtractor:
    """从受控 ``ProjectFile`` 快照生成结构化模型。

    提取器只报告源码中能找到的事实，未知信息留在 warnings 中；不根据
    项目文件中的自然语言指令扩大权限，也不把“可能需要”写成已实现能力。
    """

    def __init__(self) -> None:
        self._capability_extractor = ProjectCapabilityExtractor()
        self._hardware_engine = HardwareRuleEngine()

    def extract(
        self,
        files: Sequence[ProjectFile],
        *,
        project_name: str = "project",
        target_chip: str | None = None,
        fingerprint: str | None = None,
    ) -> ProjectModel:
        normalized_files = [
            item if isinstance(item, ProjectFile) else ProjectFile.model_validate(item)
            for item in files
        ]
        by_path = {item.path: item for item in normalized_files}
        configuration, config_facts = self._extract_configuration(
            normalized_files,
            project_name=project_name,
            target_chip=target_chip,
        )
        component_graph, component_facts = self._extract_components(normalized_files)
        capabilities = self._extract_capabilities(normalized_files)
        resources, resource_facts = self._extract_resources(normalized_files, capabilities)
        hardware_report = self._hardware_engine.inspect(
            normalized_files,
            target_chip=configuration.target_chip,
        )
        data_flows, flow_facts = self._extract_data_flows(normalized_files, component_graph)
        facts = [*config_facts, *component_facts, *resource_facts, *flow_facts]
        warnings: list[str] = []
        if "CMakeLists.txt" not in by_path:
            warnings.append("缺少根目录 CMakeLists.txt，无法确认是完整 ESP-IDF 工程")
        if not any(item.path.lower().endswith(_SOURCE_SUFFIXES) for item in normalized_files):
            warnings.append("未发现 C/C++/汇编源码，能力和数据流只能保持为空")
        if resources.has_blocking_conflict:
            warnings.append("检测到阻塞级资源冲突，写入代码前必须解决")
        if hardware_report.has_blocking_issue:
            warnings.append("检测到阻塞级硬件规则问题，写入代码前必须解决")
        return ProjectModel(
            project_name=project_name,
            target_chip=configuration.target_chip,
            fingerprint=fingerprint or _fingerprint(normalized_files),
            configuration=configuration,
            component_graph=component_graph,
            resource_graph=resources,
            hardware_report=hardware_report,
            data_flows=data_flows,
            capabilities=capabilities,
            facts=facts,
            warnings=warnings,
        )

    def _extract_configuration(
        self,
        files: Sequence[ProjectFile],
        *,
        project_name: str,
        target_chip: str | None,
    ) -> tuple[ProjectConfiguration, list[ProjectFact]]:
        cmake_paths = [item.path for item in files if PurePosixPath(item.path).name == "CMakeLists.txt"]
        kconfig_paths = [
            item.path for item in files if PurePosixPath(item.path).name.lower().startswith("kconfig")
        ]
        sdkconfig_paths = [
            item.path for item in files if PurePosixPath(item.path).name.lower().startswith("sdkconfig")
        ]
        partition_paths = [
            item.path
            for item in files
            if "partition" in PurePosixPath(item.path).name.lower()
            and PurePosixPath(item.path).suffix.lower() in {".csv", ".txt"}
        ]
        manifests = [
            item.path
            for item in files
            if PurePosixPath(item.path).name == "idf_component.yml"
        ]
        detected_target = target_chip
        detected_name = project_name
        facts: list[ProjectFact] = []
        root_cmake = next((item for item in files if item.path == "CMakeLists.txt"), None)
        if root_cmake is not None:
            project_match = _PROJECT_RE.search(root_cmake.content)
            if project_match:
                detected_name = project_match.group(1)
                facts.append(
                    self._fact(
                        "project.name",
                        "project_name",
                        {"name": detected_name},
                        [root_cmake.path],
                    )
                )
        for item in files:
            if item.path in sdkconfig_paths:
                target_match = _TARGET_RE.search(item.content)
                if target_match and detected_target is None:
                    detected_target = target_match.group(1)
                    facts.append(
                        self._fact(
                            "target.chip",
                            "target_chip",
                            {"target": detected_target},
                            [item.path],
                        )
                    )
        entries: list[dict[str, object]] = []
        for item in files:
            if item.path not in partition_paths:
                continue
            for line_number, line in enumerate(item.content.splitlines(), start=1):
                match = _PARTITION_RE.match(line)
                if not match or line.lstrip().startswith("#"):
                    continue
                entry = {
                    "name": match.group(1).strip(),
                    "type": match.group(2).strip(),
                    "subtype": match.group(3).strip(),
                    "offset": match.group(4).strip(),
                    "size": match.group(5).strip(),
                    "line": line_number,
                }
                entries.append(entry)
                facts.append(
                    self._fact(
                        f"partition:{item.path}:{line_number}",
                        "flash.partition",
                        entry,
                        [item.path],
                    )
                )
        configuration = ProjectConfiguration(
            project_name=detected_name,
            target_chip=detected_target,
            cmake_paths=sorted(cmake_paths),
            kconfig_paths=sorted(kconfig_paths),
            sdkconfig_paths=sorted(sdkconfig_paths),
            partition_paths=sorted(partition_paths),
            dependency_manifests=sorted(manifests),
            partition_entries=entries,
        )
        for path in sorted(cmake_paths):
            facts.append(self._fact(f"cmake:{path}", "build.cmake", {"path": path}, [path]))
        for path in sorted(kconfig_paths):
            facts.append(self._fact(f"kconfig:{path}", "build.kconfig", {"path": path}, [path]))
        for path in sorted(sdkconfig_paths):
            facts.append(self._fact(f"sdkconfig:{path}", "build.sdkconfig", {"path": path}, [path]))
        return configuration, facts

    def _extract_components(
        self,
        files: Sequence[ProjectFile],
    ) -> tuple[ComponentGraph, list[ProjectFact]]:
        nodes: dict[str, ComponentNode] = {}
        dependencies: list[ComponentDependency] = []
        facts: list[ProjectFact] = []
        for item in files:
            if PurePosixPath(item.path).name != "CMakeLists.txt":
                continue
            component_id = _component_id(item.path)
            component_path = PurePosixPath(item.path).parent.as_posix()
            body_match = _COMPONENT_REGISTER_RE.search(item.content)
            body = body_match.group("body") if body_match else ""
            requires = [
                *(_tokens_after_keyword(body, "REQUIRES")),
                *(_tokens_after_keyword(body, "PRIV_REQUIRES")),
            ]
            requires = list(dict.fromkeys(requires))
            source_paths = [
                path
                for path in (file.path for file in files)
                if component_path != "."
                and path.startswith(f"{component_path}/")
                and PurePosixPath(path).suffix.lower() in _SOURCE_SUFFIXES
            ]
            include_dirs = _tokens_after_keyword(body, "INCLUDE_DIRS")
            node = ComponentNode(
                component_id=component_id,
                path=item.path,
                dependencies=requires,
                source_paths=sorted(source_paths),
                public_interfaces=sorted(include_dirs),
                evidence_ids=[f"source:{item.path}"],
            )
            nodes[component_id] = node
            facts.append(
                self._fact(
                    f"component:{component_id}",
                    "idf.component",
                    {"component_id": component_id, "dependencies": requires},
                    [item.path, *source_paths],
                )
            )
            for dependency in requires:
                dependencies.append(
                    ComponentDependency(
                        source_component_id=component_id,
                        target_component_id=dependency,
                        visibility=(
                            "private"
                            if re.search(
                                rf"\bPRIV_REQUIRES\b.*?\b{re.escape(dependency)}\b",
                                body,
                                re.IGNORECASE | re.DOTALL,
                            )
                            else "public"
                        ),
                        evidence_ids=[f"source:{item.path}"],
                    )
                )
        # CMake 允许依赖 SDK 内置组件，它们未必有源码快照。把引用补成 inferred
        # 节点，使图仍可序列化，同时把不完整性写入置信度和事实中。
        for dependency in list(dependencies):
            if dependency.target_component_id not in nodes:
                nodes[dependency.target_component_id] = ComponentNode(
                    component_id=dependency.target_component_id,
                    path=f"<sdk>/{dependency.target_component_id}",
                    confidence=0.6,
                )
        return ComponentGraph(components=list(nodes.values()), dependencies=dependencies), facts

    def _extract_capabilities(self, files: Sequence[ProjectFile]) -> list[ProjectCapability]:
        capabilities = self._capability_extractor.extract(files)
        by_id = {capability.capability_id: capability for capability in capabilities}
        feature_patterns: list[tuple[str, str, tuple[str, ...]]] = [
            ("bus.i2c", "bus.i2c", ("i2c_master", "i2c_param_config", "I2C_NUM_")),
            ("bus.spi", "bus.spi", ("spi_bus_initialize", "spi_device_", "SPI2_HOST", "SPI3_HOST")),
            ("bus.uart", "bus.uart", ("uart_driver_install", "uart_param_config", "UART_NUM_")),
            ("bus.twai", "bus.twai", ("twai_driver_install", "twai_general_config", "CAN")),
            ("network.wifi", "network.wifi", ("esp_wifi_init", "esp_wifi_start", "WIFI_MODE_")),
            ("network.ble", "network.ble", ("esp_ble", "nimble_port", "BLE_GATT")),
            ("network.mqtt_client", "network.mqtt_client", ("mqtt_client", "esp_mqtt", "MQTT_EVENT_")),
            ("network.http", "network.http_client", ("esp_http_client", "httpd_start", "HTTPD_")),
            ("protocol.modbus_rtu", "protocol.modbus_rtu", ("modbus", "MODBUS_RTU", "rs485")),
            ("storage.nvs_config", "storage.nvs_config", ("nvs_open", "nvs_set", "nvs_get")),
            ("system.ota", "system.ota", ("esp_ota", "ota_begin", "OTA_")),
            ("sync.queue", "sync.queue", ("xQueueCreate", "xQueueSend", "xQueueReceive")),
            ("sync.event_group", "sync.event_group", ("xEventGroupCreate", "xEventGroupSetBits")),
            ("sync.mutex", "sync.mutex", ("xSemaphoreCreateMutex", "xSemaphoreTake")),
        ]
        for capability_id, kind, patterns in feature_patterns:
            matched_paths = [
                item.path
                for item in files
                if any(pattern.casefold() in item.content.casefold() for pattern in patterns)
            ]
            if not matched_paths:
                continue
            owners = sorted({_owner_for_path(path) for path in matched_paths})
            by_id[capability_id] = ProjectCapability(
                capability_id=capability_id,
                kind=kind,
                parameters={"matched_patterns": list(patterns)},
                owners=owners,
                evidence_ids=[f"source:{path}" for path in matched_paths],
                source_paths=matched_paths,
                source_kind="source",
                confidence=0.95,
            )
        task_matches: dict[str, list[str]] = defaultdict(list)
        for item in files:
            for match in _TASK_RE.finditer(item.content):
                task_matches[match.group(1)].append(item.path)
        for task_name, paths in sorted(task_matches.items()):
            capability_id = f"task.freertos:{task_name}"
            by_id[capability_id] = ProjectCapability(
                capability_id=capability_id,
                kind="task.freertos",
                parameters={"entry_function": task_name},
                owners=sorted({_owner_for_path(path) for path in paths}),
                evidence_ids=[f"source:{path}:task:{task_name}" for path in paths],
                source_paths=sorted(set(paths)),
                confidence=0.95,
            )
        return [by_id[key] for key in sorted(by_id)]

    def _extract_resources(
        self,
        files: Sequence[ProjectFile],
        capabilities: Sequence[ProjectCapability],
    ) -> tuple[ResourceGraph, list[ProjectFact]]:
        allocations: list[ResourceAllocation] = []
        facts: list[ProjectFact] = []
        for capability in capabilities:
            if capability.kind == "gpio.output":
                pin = capability.parameters.get("pin")
                if not isinstance(pin, int):
                    continue
                for source_path in capability.source_paths or ["<inferred>"]:
                    owner = f"{capability.capability_id}@{_owner_for_path(source_path)}"
                    resource_id = f"gpio:P{pin}"
                    allocation = ResourceAllocation(
                        resource_id=resource_id,
                        resource_kind="pin",
                        owner_capability_id=owner,
                        parameters={"pin": pin, "mode": "output"},
                        shared=False,
                        constraints=["GPIO 输出不能与其他独立功能复用"],
                        evidence_ids=capability.evidence_ids,
                    )
                    allocations.append(allocation)
                    facts.append(
                        self._fact(
                            f"resource:{resource_id}:{source_path}",
                            "hardware.gpio",
                            {"resource_id": resource_id, "pin": pin, "owner": owner},
                            [source_path],
                        )
                    )
            elif capability.kind.startswith("bus."):
                bus_name = capability.kind.removeprefix("bus.")
                resource_id = f"{bus_name}:default"
                allocations.append(
                    ResourceAllocation(
                        resource_id=resource_id,
                        resource_kind="bus_controller",
                        owner_capability_id=capability.capability_id,
                        parameters={"bus": bus_name},
                        shared=True,
                        constraints=["共享总线必须分别校验地址、片选和时序"],
                        evidence_ids=capability.evidence_ids,
                    )
                )
        conflicts: list[ResourceConflict] = []
        grouped: dict[str, list[ResourceAllocation]] = defaultdict(list)
        for allocation in allocations:
            grouped[allocation.resource_id].append(allocation)
        for resource_id, values in sorted(grouped.items()):
            owners = sorted({value.owner_capability_id for value in values})
            if len(owners) < 2 or all(value.shared for value in values):
                continue
            conflicts.append(
                ResourceConflict(
                    resource_ids=[resource_id],
                    severity="blocking",
                    reason=f"资源 {resource_id} 被多个能力占用：{', '.join(owners)}",
                    alternatives=["重新分配引脚", "确认是否确实需要复用该资源"],
                )
            )
        return ResourceGraph(allocations=allocations, conflicts=conflicts), facts

    def _extract_data_flows(
        self,
        files: Sequence[ProjectFile],
        component_graph: ComponentGraph,
    ) -> tuple[list[DataFlow], list[ProjectFact]]:
        nodes: dict[str, DataFlowNode] = {}
        edges: list[DataFlowEdge] = []
        facts: list[ProjectFact] = []
        all_content = "\n".join(item.content for item in files)
        source_paths = [item.path for item in files if item.path.lower().endswith(_SOURCE_SUFFIXES)]

        def add_node(node_id: str, kind: str, *, component_id: str | None = None, paths: Iterable[str] = ()) -> None:
            if node_id in nodes:
                return
            path_list = list(paths)
            nodes[node_id] = DataFlowNode(
                node_id=node_id,
                kind=kind,
                component_id=component_id,
                evidence_ids=[f"source:{path}" for path in path_list],
            )

        def add_edge(source: str, target: str, data_type: str, paths: Iterable[str] = ()) -> None:
            if not any(
                edge.source_node_id == source
                and edge.target_node_id == target
                and edge.data_type == data_type
                for edge in edges
            ):
                path_list = list(paths)
                edges.append(
                    DataFlowEdge(
                        source_node_id=source,
                        target_node_id=target,
                        data_type=data_type,
                        synchronization=("queue" if "Queue" in all_content else None),
                        evidence_ids=[f"source:{path}" for path in path_list],
                    )
                )

        sensor_paths = [
            path
            for path, content in ((item.path, item.content) for item in files)
            if re.search(r"sht3[01]|dht\d*|temperature|humidity|sensor", content, re.IGNORECASE)
        ]
        if sensor_paths:
            sensor_name = "sensor.sht30" if any(
                re.search(r"sht3[01]", next(item.content for item in files if item.path == path), re.IGNORECASE)
                for path in sensor_paths
            ) else "sensor"
            add_node(sensor_name, "device.sensor", paths=sensor_paths)
            if "i2c" in all_content.casefold():
                add_node("bus.i2c", "bus.i2c", paths=source_paths)
                add_edge(sensor_name, "bus.i2c", "sensor_reading", sensor_paths)
        task_names = sorted(set(_TASK_RE.findall(all_content)))
        for task_name in task_names:
            node_id = f"task:{task_name}"
            add_node(node_id, "task.freertos", paths=source_paths)
            if sensor_paths and "i2c" in all_content.casefold():
                add_edge("bus.i2c", node_id, "sensor_reading", source_paths)
        if re.search(r"xQueue(Create|Send|Receive)", all_content):
            add_node("queue:data", "sync.queue", paths=source_paths)
            for task_name in task_names:
                add_edge(f"task:{task_name}", "queue:data", "sample", source_paths)
        display_paths = [
            path
            for path, content in ((item.path, item.content) for item in files)
            if re.search(r"ssd1306|oled|tft|display", content, re.IGNORECASE)
        ]
        if display_paths:
            add_node("display", "device.display", paths=display_paths)
            for task_name in task_names:
                add_edge(f"task:{task_name}", "display", "display_frame", display_paths)
        if re.search(r"mqtt|esp_mqtt", all_content, re.IGNORECASE):
            add_node("network:mqtt", "network.mqtt", paths=source_paths)
            for task_name in task_names:
                add_edge(f"task:{task_name}", "network:mqtt", "telemetry", source_paths)
        if re.search(r"nvs_open|nvs_set|nvs_get", all_content, re.IGNORECASE):
            add_node("storage:nvs", "storage.nvs", paths=source_paths)
            for task_name in task_names:
                add_edge(f"task:{task_name}", "storage:nvs", "configuration", source_paths)

        if not nodes:
            return [], facts
        flow = DataFlow(flow_id="project.default", nodes=list(nodes.values()), edges=edges)
        for node in flow.nodes:
            facts.append(
                self._fact(
                    f"dataflow:{node.node_id}",
                    "runtime.data_flow_node",
                    {"node_id": node.node_id, "kind": node.kind},
                    [evidence.removeprefix("source:") for evidence in node.evidence_ids],
                )
            )
        return [flow], facts

    @staticmethod
    def _fact(
        fact_id: str,
        kind: str,
        value: dict[str, object],
        source_paths: Sequence[str],
        *,
        confidence: float = 1.0,
    ) -> ProjectFact:
        paths = sorted({path for path in source_paths if not path.startswith("<")})
        return ProjectFact(
            fact_id=fact_id,
            kind=kind,
            value=value,
            source_kind="source",
            source_paths=paths,
            evidence_ids=[f"source:{path}" for path in paths],
            confidence=confidence,
        )
