"""插件配置解析。

把 AstrBot 配置面板里的原始值（可能是字符串）转换成强类型设置，
所有数值都在这里收敛到安全区间，避免面板填错导致插件异常。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .cleaner import DEFAULT_KEYWORDS, CleanSettings
from .view import DEFAULT_FORWARD_PLATFORMS

_TRUE = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "off", "disable", "disabled"}
_LIST_SEPARATORS = ("\n", "，", ",", "、", "|")

SESSION_MODES = ("all", "whitelist", "blacklist")
DEFAULT_ROLES = ("assistant",)


def _lookup(mapping: Any, key: str, default: Any = None) -> Any:
    if mapping is None:
        return default
    try:
        value = mapping.get(key, default)
    except (AttributeError, TypeError):
        return default
    return default if value is None else value


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return default


def as_int(value: Any, default: int = 0, *, minimum: int | None = None) -> int:
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def as_str_list(value: Any) -> tuple[str, ...]:
    """把配置值整理成字符串元组，兼容列表与分隔符文本。"""
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    items: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            raw = str(raw)
        segments = [raw]
        for separator in _LIST_SEPARATORS:
            segments = [piece for seg in segments for piece in seg.split(separator)]
        for segment in segments:
            text = segment.strip()
            if text and text not in items:
                items.append(text)
    return tuple(items)


def as_choice(value: Any, options: tuple[str, ...], default: str) -> str:
    text = as_text(value, default).strip().lower()
    return text if text in options else default


@dataclass(frozen=True)
class ViewSettings:
    """``/think view`` 的相关参数。"""

    default_count: int = 10
    max_count: int = 50
    max_chars: int = 0
    use_forward: bool = True
    forward_platforms: tuple[str, ...] = DEFAULT_FORWARD_PLATFORMS
    node_name: str = "think"
    node_max_chars: int = 3000
    plain_chunk_chars: int = 1200


@dataclass(frozen=True)
class PluginSettings:
    """插件运行期使用的全部设置。"""

    enabled: bool = True
    admin_only: bool = False
    session_mode: str = "all"
    sessions: tuple[str, ...] = ()
    roles: tuple[str, ...] = DEFAULT_ROLES
    dry_run: bool = False
    backup_before_write: bool = True
    backup_limit: int = 10
    max_messages: int = 0
    max_block_chars: int = 0
    drop_empty_think: bool = True
    clear_thinking_signature: bool = True
    report_top_blocks: int = 5
    clean: CleanSettings = field(default_factory=CleanSettings)
    view: ViewSettings = field(default_factory=ViewSettings)

    def session_allowed(self, umo: str) -> bool:
        if self.session_mode == "whitelist":
            return umo in self.sessions
        if self.session_mode == "blacklist":
            return umo not in self.sessions
        return True


def load_settings(config: Any) -> PluginSettings:
    """从插件配置字典读取设置，缺省值与 _conf_schema.json 保持一致。"""
    if not isinstance(config, Mapping):
        try:
            config = dict(config or {})
        except (TypeError, ValueError):
            config = {}

    keywords = as_str_list(_lookup(config, "keywords", DEFAULT_KEYWORDS))
    if not keywords:
        keywords = DEFAULT_KEYWORDS

    clean = CleanSettings(
        keywords=keywords,
        extra_regex=as_str_list(_lookup(config, "keyword_regex", ())),
        ignore_case=as_bool(_lookup(config, "ignore_case", True), True),
        anchor_single_char=as_bool(_lookup(config, "anchor_single_char", True), True),
        strip_inline=as_bool(_lookup(config, "strip_inline_keywords", True), True),
        drop_keyword_only=as_bool(
            _lookup(config, "drop_keyword_only_paragraphs", True), True
        ),
        max_paragraph_repeats=as_int(
            _lookup(config, "max_paragraph_repeats", 2), 2, minimum=1
        ),
        near_dup_enabled=as_bool(
            _lookup(_lookup(config, "near_duplicate", {}), "enabled", True), True
        ),
        near_dup_max_chars=as_int(
            _lookup(_lookup(config, "near_duplicate", {}), "max_chars", 40),
            40,
            minimum=4,
        ),
        near_dup_threshold=as_float(
            _lookup(_lookup(config, "near_duplicate", {}), "threshold", 0.80), 0.80
        ),
        near_dup_length_ratio=as_float(
            _lookup(_lookup(config, "near_duplicate", {}), "length_ratio", 0.35), 0.35
        ),
        dedupe_lines=as_bool(_lookup(config, "dedupe_lines", True), True),
        max_line_repeats=as_int(_lookup(config, "max_line_repeats", 1), 1, minimum=1),
        dedupe_sentences=as_bool(_lookup(config, "dedupe_sentences", False), False),
        max_sentence_repeats=as_int(
            _lookup(config, "max_sentence_repeats", 1), 1, minimum=1
        ),
    )

    roles = tuple(
        role.lower()
        for role in as_str_list(_lookup(config, "roles", DEFAULT_ROLES))
        if role
    )

    forward_platforms = as_str_list(
        _lookup(config, "view_forward_platforms", DEFAULT_FORWARD_PLATFORMS)
    )
    view = ViewSettings(
        default_count=as_int(_lookup(config, "view_default_count", 10), 10, minimum=0),
        max_count=as_int(_lookup(config, "view_max_count", 50), 50, minimum=0),
        max_chars=as_int(_lookup(config, "view_max_chars_per_block", 0), 0, minimum=0),
        use_forward=as_bool(_lookup(config, "view_use_forward", True), True),
        # 空列表表示不限制平台，交给底层适配器尝试合并转发
        forward_platforms=forward_platforms,
        node_name=as_text(_lookup(config, "view_node_name", "think"), "think").strip()
        or "think",
        node_max_chars=as_int(
            _lookup(config, "view_node_max_chars", 3000), 3000, minimum=0
        ),
        plain_chunk_chars=as_int(
            _lookup(config, "view_plain_chunk_chars", 1200), 1200, minimum=200
        ),
    )

    return PluginSettings(
        enabled=as_bool(_lookup(config, "enabled", True), True),
        admin_only=as_bool(_lookup(config, "admin_only", False), False),
        session_mode=as_choice(
            _lookup(config, "session_mode", "all"), SESSION_MODES, "all"
        ),
        sessions=as_str_list(_lookup(config, "sessions", ())),
        roles=roles or DEFAULT_ROLES,
        dry_run=as_bool(_lookup(config, "dry_run", False), False),
        backup_before_write=as_bool(_lookup(config, "backup_before_write", True), True),
        backup_limit=as_int(_lookup(config, "backup_limit", 10), 10, minimum=0),
        max_messages=as_int(_lookup(config, "max_messages", 0), 0, minimum=0),
        max_block_chars=as_int(_lookup(config, "max_block_chars", 0), 0, minimum=0),
        drop_empty_think=as_bool(_lookup(config, "drop_empty_think", True), True),
        clear_thinking_signature=as_bool(
            _lookup(config, "clear_thinking_signature", True), True
        ),
        report_top_blocks=as_int(_lookup(config, "report_top_blocks", 5), 5, minimum=0),
        clean=clean,
        view=view,
    )
