"""Pydantic request schemas for MCP tool parameters.

Plain data carriers for validating tool-call input, not domain or persistence
models — see prosuite.data_model.Model for ProSuite's own, unrelated "Model"
concept.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DatasetRef(BaseModel):
    name: str
    filter_expression: str = ""


class ConditionRequest(BaseModel):
    condition: str
    params: dict[str, Any] = {}


class WorkspaceReplacement(BaseModel):
    workspace_id: str
    workspace_path: str
