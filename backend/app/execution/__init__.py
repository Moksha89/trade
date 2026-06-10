"""Execution layer: paper simulation and Capital.com (demo/live) executors."""

from app.execution.base import ExecutionResult, Executor
from app.execution.factory import get_executor

__all__ = ["ExecutionResult", "Executor", "get_executor"]
