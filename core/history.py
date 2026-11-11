"""对话历史中 think 片段的读取、清洗与回写。

AstrBot 把助手消息的思考内容存成 ``{"type": "think", "think": "...", "encrypted": "..."}``
这样的 content part，本模块只替换这些 part，其它字段原样保留。
清洗过程不会修改传入的历史，而是返回一份新的列表，方便写回前先备份原文。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cleaner import BlockResult, ThinkCleaner

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


class HistoryError(RuntimeError):
    """读取或写回对话历史失败。"""


@dataclass(frozen=True)
class TopBlock:
    """删除量最大的 think 块，用于报告。"""

    message_index: int
    before: int
    after: int
    preview: str


@dataclass
class TidyReport:
    """一次整理的统计结果。"""

    conv_id: str = ""
    messages: int = 0
    scanned_messages: int = 0
    think_blocks: int = 0
    changed_blocks: int = 0
    before: int = 0
    after: int = 0
    keyword_hits: int = 0
    dropped_paragraphs: int = 0
    dropped_lines: int = 0
    dropped_sentences: int = 0
    removed_parts: int = 0
    cleared_signatures: int = 0
    skipped_large_blocks: int = 0
    top_blocks: list[TopBlock] = field(default_factory=list)

    @property
    def saved_chars(self) -> int:
        return max(0, self.before - self.after)

    @property
    def saved_ratio(self) -> float:
        return (self.saved_chars / self.before) if self.before else 0.0

    @property
    def changed(self) -> bool:
        return self.changed_blocks > 0 or self.removed_parts > 0


def parse_history(raw: Any) -> list[Any]:
    """把对话记录解析成消息列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HistoryError(f"对话记录不是合法 JSON：{exc}") from exc
        if not isinstance(data, list):
            raise HistoryError("对话记录格式异常：顶层不是列表")
        return data
    raise HistoryError(f"无法识别的对话记录类型：{type(raw).__name__}")


@dataclass(frozen=True)
class ThinkBlock:
    """用于查看的一个 think 块。"""

    message_index: int
    """1-based，在整段对话历史中的位置。"""
    part_index: int
    """1-based，在该条消息内的位置。"""
    role: str
    chars: int
    """原始字数。"""
    text: str
    truncated: bool = False

    @property
    def header(self) -> str:
        mark = (
            f"（已截断，原 {self.chars} 字）" if self.truncated else f"{self.chars} 字"
        )
        return f"[#{self.message_index}.{self.part_index}] {self.role} · {mark}"


@dataclass
class ViewResult:
    """一次查看的结果。"""

    conv_id: str = ""
    messages: int = 0
    scanned_messages: int = 0
    blocks: list[ThinkBlock] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(block.chars for block in self.blocks)

    @property
    def truncated_blocks(self) -> int:
        return sum(1 for block in self.blocks if block.truncated)


def collect_think_blocks(
    history: list[Any],
    *,
    roles: tuple[str, ...],
    count: int = 0,
    max_chars: int = 0,
) -> ViewResult:
    """收集最近 ``count`` 条消息里的 think 块（按时间正序返回）。

    Args:
        history: 完整对话历史。
        roles: 只保留这些角色的消息（不区分大小写）。
        count: 只扫描最近多少条消息；0 表示全部。
        max_chars: 单个块的最大展示字数；0 表示不截断。
    """
    result = ViewResult(messages=len(history))
    allowed_roles = {role.lower() for role in roles} or {"assistant"}

    start = 0
    if count > 0 and len(history) > count:
        start = len(history) - count

    for index in range(start, len(history)):
        message = history[index]
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).lower()
        if role not in allowed_roles:
            continue
        result.scanned_messages += 1

        content = message.get("content")
        if not isinstance(content, list):
            continue

        part_seq = 0
        for part in content:
            if not (
                isinstance(part, dict)
                and part.get("type") == "think"
                and isinstance(part.get("think"), str)
                and part["think"].strip()
            ):
                continue
            part_seq += 1
            text = part["think"]
            chars = len(text)
            truncated = max_chars > 0 and chars > max_chars
            result.blocks.append(
                ThinkBlock(
                    message_index=index + 1,
                    part_index=part_seq,
                    role=role,
                    chars=chars,
                    text=(text[:max_chars] + "…") if truncated else text,
                    truncated=truncated,
                )
            )

    return result


def tidy_messages(
    history: list[Any],
    cleaner: ThinkCleaner,
    *,
    roles: tuple[str, ...],
    max_messages: int = 0,
    max_block_chars: int = 0,
    drop_empty_think: bool = True,
    clear_signature: bool = True,
    report_top: int = 5,
) -> tuple[list[Any], TidyReport]:
    """返回 (清洗后的新历史, 统计)，不修改传入的 ``history``。"""
    report = TidyReport(messages=len(history))
    allowed_roles = {role.lower() for role in roles} or {"assistant"}
    new_history: list[Any] = list(history)

    start = 0
    if max_messages > 0 and len(history) > max_messages:
        start = len(history) - max_messages

    for index in range(start, len(history)):
        message = history[index]
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "")).lower() not in allowed_roles:
            continue
        content = message.get("content")
        if not isinstance(content, list) or not content:
            continue
        report.scanned_messages += 1

        new_parts: list[Any] = []
        emptied: set[int] = set()
        for part in content:
            if not (
                isinstance(part, dict)
                and part.get("type") == "think"
                and isinstance(part.get("think"), str)
                and part["think"].strip()
            ):
                new_parts.append(part)
                continue

            value = part["think"]
            if max_block_chars > 0 and len(value) > max_block_chars:
                report.skipped_large_blocks += 1
                new_parts.append(part)
                continue

            result = cleaner.clean(value)
            report.think_blocks += 1
            report.before += result.before
            report.after += result.after
            report.keyword_hits += result.keyword_hits
            report.dropped_paragraphs += result.dropped_paragraphs
            report.dropped_lines += result.dropped_lines
            report.dropped_sentences += result.dropped_sentences

            if not result.changed:
                new_parts.append(part)
                continue

            report.changed_blocks += 1
            updated = dict(part)
            updated["think"] = result.text
            if clear_signature and updated.get("encrypted"):
                updated["encrypted"] = None
                report.cleared_signatures += 1
            new_parts.append(updated)
            if not result.text.strip():
                emptied.add(len(new_parts) - 1)
            _track_top_block(report, index, result, report_top)

        if drop_empty_think and emptied:
            new_parts, removed = _drop_empty_think_parts(new_parts, emptied)
            report.removed_parts += removed

        if len(new_parts) != len(content) or any(
            new is not old for new, old in zip(new_parts, content)
        ):
            updated_message = dict(message)
            updated_message["content"] = new_parts
            new_history[index] = updated_message

    if report_top > 0:
        report.top_blocks.sort(key=lambda item: item.before - item.after, reverse=True)
        del report.top_blocks[report_top:]
    return new_history, report


def _track_top_block(
    report: TidyReport, index: int, result: BlockResult, report_top: int
) -> None:
    if report_top <= 0:
        return
    preview = " ".join(result.text.split())[:40]
    report.top_blocks.append(
        TopBlock(
            message_index=index + 1,
            before=result.before,
            after=result.after,
            preview=preview,
        )
    )


def _drop_empty_think_parts(
    parts: list[Any], emptied: set[int]
) -> tuple[list[Any], int]:
    """把本轮清洗成空串的 think part 摘掉，但至少保留一个 part。"""
    filtered = [part for index, part in enumerate(parts) if index not in emptied]
    removed = len(parts) - len(filtered)
    if not filtered or removed == 0:
        return parts, 0
    return filtered, removed


def safe_dir_name(umo: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", umo).strip("_")[:60] or "session"
    digest = hashlib.sha1(umo.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{cleaned}_{digest}"


class HistoryStore:
    """封装对话历史的读取、备份与写回。"""

    def __init__(
        self,
        conversation_manager: Any,
        backup_root: Path | None = None,
        logger: Any = None,
    ) -> None:
        self._conversation_manager = conversation_manager
        self._backup_root = backup_root
        self._logger = logger

    async def load(self, umo: str) -> tuple[str, list[Any]]:
        manager = self._conversation_manager
        if manager is None:
            raise HistoryError("当前 AstrBot 版本不支持读取对话历史")

        conv_id = await manager.get_curr_conversation_id(umo)
        if not conv_id:
            raise HistoryError("当前会话还没有对话记录")

        conversation = await manager.get_conversation(umo, conv_id)
        if conversation is None:
            raise HistoryError("未能读取当前对话")

        history = parse_history(getattr(conversation, "history", None))
        return conv_id, history

    async def save(self, umo: str, conv_id: str, history: list[Any]) -> None:
        manager = self._conversation_manager
        if manager is None:
            raise HistoryError("当前 AstrBot 版本不支持写回对话历史")
        await manager.update_conversation(umo, conv_id, history=history)

    async def backup(self, umo: str, conv_id: str, history: list[Any]) -> Path | None:
        """写回前把原始历史另存一份，返回备份文件路径。"""
        if self._backup_root is None:
            return None
        try:
            directory = self._backup_root / safe_dir_name(umo)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = directory / f"{stamp}_{conv_id[:8]}.json"
            payload = json.dumps(history, ensure_ascii=False, indent=2)
            await asyncio.get_running_loop().run_in_executor(
                None, _write_text, path, payload
            )
            return path
        except Exception as exc:  # noqa: BLE001
            if self._logger is not None:
                self._logger.warning(f"[repeat_delete] 备份对话历史失败: {exc}")
            return None

    def prune_backups(self, umo: str, limit: int) -> None:
        if self._backup_root is None or limit <= 0:
            return
        try:
            directory = self._backup_root / safe_dir_name(umo)
            if not directory.is_dir():
                return
            files = sorted(
                (item for item in directory.iterdir() if item.is_file()),
                key=lambda item: item.stat().st_mtime,
            )
            for stale in files[:-limit]:
                stale.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            if self._logger is not None:
                self._logger.debug(f"[repeat_delete] 清理旧备份失败: {exc}")


def _write_text(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")
