"""Gommage Adapters Package."""

from recorder.adapters.base import BaseAdapter, BaseLLMAdapter, BaseToolAdapter
from recorder.adapters.function_adapter import gommage_tool, FunctionToolAdapter
from recorder.adapters.mcp_adapter import MCPAdapter

__all__ = [
    "BaseAdapter",
    "BaseLLMAdapter",
    "BaseToolAdapter",
    "gommage_tool",
    "FunctionToolAdapter",
    "MCPAdapter",
]


try:
    from recorder.adapters.langchain_adapter import (
        GommageLangChainCallbackHandler,
        GommageLangChainToolWrapper,
    )
except ImportError:
    pass
else:
    __all__ += ["GommageLangChainCallbackHandler", "GommageLangChainToolWrapper"]

try:
    from recorder.adapters.langgraph_adapter import LangGraphNodeInterceptor
except ImportError:
    pass
else:
    __all__.append("LangGraphNodeInterceptor")

try:
    from recorder.adapters.replay_adapter import ReplayChatModel, ReplayTool
except ImportError:
    pass
else:
    __all__ += ["ReplayChatModel", "ReplayTool"]
