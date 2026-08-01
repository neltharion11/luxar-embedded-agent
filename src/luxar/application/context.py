from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from luxar.ports.espidf import EspIdfPort
from luxar.ports.planner import Planner
from luxar.ports.requirement_parser import RequirementParser


@dataclass(frozen=True)
class RuntimeContext:
    requirement_parser: RequirementParser
    planner: Planner
    espidf: EspIdfPort
    project_path: Path