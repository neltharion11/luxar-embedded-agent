from __future__ import annotations

from pydantic import BaseModel, Field


class RuntimeRunRequest(BaseModel):
    task: str = Field(default="")
    project: str = Field(default="")


class ArtifactManageRequest(BaseModel):
    action: str
    name: str
    category: str = ""
    content: str = ""
    old_string: str = ""
    new_string: str = ""
