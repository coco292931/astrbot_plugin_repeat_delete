"""think 去重与查看插件的内部模块。"""

from .cleaner import CleanSettings, ThinkCleaner
from .config import PluginSettings, ViewSettings, load_settings
from .history import (
    HistoryStore,
    ThinkBlock,
    TidyReport,
    ViewResult,
    collect_think_blocks,
    tidy_messages,
)
from .view import forward_components, plain_chunks, supports_forward

__all__ = [
    "CleanSettings",
    "HistoryStore",
    "PluginSettings",
    "ThinkBlock",
    "ThinkCleaner",
    "TidyReport",
    "ViewResult",
    "ViewSettings",
    "collect_think_blocks",
    "forward_components",
    "load_settings",
    "plain_chunks",
    "supports_forward",
    "tidy_messages",
]
