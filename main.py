"""think 去重插件入口。

手动触发的会话历史清理：``/think tidy`` 直接改写当前会话对话记录里的 think 片段，
把 repeat-detect 里那套「关键词剥离 + 重复折叠」跑在真实的 AstrBot 历史上。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.cleaner import ThinkCleaner
from .core.config import PluginSettings, load_settings
from .core.history import (
    HistoryError,
    HistoryStore,
    TidyReport,
    collect_think_blocks,
    tidy_messages,
)
from .core.view import (
    forward_components,
    plain_chunks,
    scope_label,
    supports_forward,
)

try:  # AstrBot 4.22+ 用同一把会话锁串行化同会话的 LLM 请求
    from astrbot.core.utils.session_lock import session_lock_manager
except Exception:  # pragma: no cover - 老版本或裁剪过的发行版
    session_lock_manager = None

PLUGIN_NAME = "astrbot_plugin_repeat_delete"
PLUGIN_VERSION = "1.0.0"
PLUGIN_REPO = "https://github.com/coco292931/astrbot_plugin_repeat_delete"
SESSION_LOCK_TIMEOUT = 20.0

TIDY_ALIASES = {"整理", "clean"}
PREVIEW_ALIASES = {"预览", "check", "dry"}
VIEW_ALIASES = {"查看", "show", "ls"}


@register(
    PLUGIN_NAME,
    "coco",
    "整理当前会话 think 中的重复段落与无意义关键词",
    PLUGIN_VERSION,
    PLUGIN_REPO,
)
class RepeatDeletePlugin(Star):
    """按需清理当前会话历史里 think 部分的水分。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context, config)
        self._config = config if config is not None else {}
        self._backup_root: Path | None = None

    async def initialize(self) -> None:
        self._backup_root = self._resolve_backup_root()
        settings = load_settings(self._config)
        self.logger.info(
            "[repeat_delete] 已加载：启用=%s 关键词=%d 段落保留=%d 近似阈值=%.2f 备份=%s",
            settings.enabled,
            len(settings.clean.keywords),
            settings.clean.max_paragraph_repeats,
            settings.clean.near_dup_threshold,
            settings.backup_before_write,
        )

    async def terminate(self) -> None:
        self._backup_root = None

    # ---------------- 指令 ----------------

    @filter.command_group("think", alias={"THINK"})
    def think(self):
        """查看或整理对话历史中的 think（思考）内容。"""

    @think.command("tidy", alias=TIDY_ALIASES)
    async def tidy(self, event: AstrMessageEvent):
        """清洗本会话历史里的 think：删关键词噪声、折叠重复段落。

        用法：/think tidy [最近消息数]
        """
        async for result in self._handle(event, preview=False):
            yield result

    @think.command("preview", alias=PREVIEW_ALIASES)
    async def preview(self, event: AstrMessageEvent):
        """只统计不写入，先确认 /think tidy 会删掉什么。

        用法：/think preview [最近消息数]
        """
        async for result in self._handle(event, preview=True):
            yield result

    @think.command("view", alias=VIEW_ALIASES)
    async def view(self, event: AstrMessageEvent):
        """查看最近 n 条消息里的 think 原文，尽量以合并转发发送。

        用法：/think view [最近消息数]
        """
        async for result in self._handle_view(event):
            yield result

    # ---------------- 主流程 ----------------

    async def _handle(self, event: AstrMessageEvent, preview: bool):
        # 注意：不能调用 event.stop_event()。AstrBot 的洋葱式管道一旦发现事件被终止
        # 就会跳过后续的 ResultDecorate / Respond 阶段，连 yield 出去的回复都不会发送。
        # 命令响应发出后 event._has_send_oper 会被置位，默认 LLM 请求自然被跳过。
        settings = load_settings(self._config)

        blocked = self._check_access(event, settings)
        if blocked:
            yield event.plain_result(blocked)
            return

        preview = preview or settings.dry_run
        limit = self._trailing_number(event.message_str, settings.max_messages)
        cleaner = ThinkCleaner(settings.clean)
        umo = event.unified_msg_origin
        store = HistoryStore(
            getattr(self.context, "conversation_manager", None),
            self._backup_root,
            self.logger,
        )

        started = time.perf_counter()
        try:
            async with _session_guard(umo, self.logger):
                conv_id, history = await store.load(umo)
                new_history, report = tidy_messages(
                    history,
                    cleaner,
                    roles=settings.roles,
                    max_messages=limit,
                    max_block_chars=settings.max_block_chars,
                    drop_empty_think=settings.drop_empty_think,
                    clear_signature=settings.clear_thinking_signature,
                    report_top=settings.report_top_blocks,
                )
                report.conv_id = conv_id

                backup_path: Path | None = None
                if not preview and report.changed:
                    if settings.backup_before_write:
                        backup_path = await store.backup(umo, conv_id, history)
                    await store.save(umo, conv_id, new_history)
                    if settings.backup_before_write:
                        store.prune_backups(umo, settings.backup_limit)
        except HistoryError as exc:
            yield event.plain_result(f"❌ {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"[repeat_delete] 整理 think 失败: {exc}", exc_info=True)
            yield event.plain_result(f"❌ 整理失败：{exc}")
            return

        elapsed = time.perf_counter() - started
        yield event.plain_result(
            _format_report(
                settings,
                report,
                preview=preview,
                elapsed=elapsed,
                backup_path=backup_path,
            )
        )
        self.logger.info(
            "[repeat_delete] %s conv=%s blocks=%d %d->%d chars, %.2fs",
            "预览" if preview else "写回",
            conv_id,
            report.think_blocks,
            report.before,
            report.after,
            elapsed,
        )

    # ---------------- 查看 think ----------------

    async def _handle_view(self, event: AstrMessageEvent):
        # 与 tidy 一样不调用 stop_event()，否则管道会跳过 Respond 阶段
        settings = load_settings(self._config)
        view_cfg = settings.view

        blocked = self._check_access(event, settings)
        if blocked:
            yield event.plain_result(blocked)
            return

        count = self._trailing_number(event.message_str, view_cfg.default_count)
        if count > 0 and view_cfg.max_count > 0:
            count = min(count, view_cfg.max_count)

        umo = event.unified_msg_origin
        store = HistoryStore(
            getattr(self.context, "conversation_manager", None),
            self._backup_root,
            self.logger,
        )
        try:
            conv_id, history = await store.load(umo)
        except HistoryError as exc:
            yield event.plain_result(f"❌ {exc}")
            return

        view = collect_think_blocks(
            history, roles=settings.roles, count=count, max_chars=view_cfg.max_chars
        )
        view.conv_id = conv_id

        if not view.blocks:
            yield event.plain_result(f"{scope_label(count)}里没有 think 内容")
            return

        platform = ""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            platform = str(getter() or "")

        if view_cfg.use_forward and supports_forward(
            platform, view_cfg.forward_platforms
        ):
            components = forward_components(
                view,
                count=count,
                node_name=view_cfg.node_name,
                node_max_chars=view_cfg.node_max_chars,
            )
            if components:
                try:
                    await event.send(MessageChain(chain=components))
                    self.logger.info(
                        "[repeat_delete] 以合并转发查看 think：conv=%s blocks=%d chars=%d",
                        conv_id,
                        len(view.blocks),
                        view.total_chars,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        f"[repeat_delete] 合并转发发送失败，回退纯文本: {exc}"
                    )

        for chunk in plain_chunks(
            view, count=count, chunk_chars=view_cfg.plain_chunk_chars
        ):
            yield event.plain_result(chunk)

        self.logger.info(
            "[repeat_delete] 以纯文本查看 think：conv=%s blocks=%d chars=%d",
            conv_id,
            len(view.blocks),
            view.total_chars,
        )

    # ---------------- 辅助 ----------------

    def _check_access(self, event: AstrMessageEvent, settings: PluginSettings) -> str:
        if not settings.enabled:
            return "⛔ think 功能已在插件配置中关闭。"

        if settings.admin_only:
            is_admin = getattr(event, "is_admin", None)
            allowed = bool(is_admin()) if callable(is_admin) else event.role == "admin"
            if not allowed:
                return "⛔ 该功能当前仅管理员可用（可在插件配置中关闭「仅管理员」）。"

        umo = event.unified_msg_origin
        if not settings.session_allowed(umo):
            label = "白名单" if settings.session_mode == "whitelist" else "黑名单"
            return f"⛔ 当前会话不在{label}范围内：{umo}"
        return ""

    @staticmethod
    def _trailing_number(message_str: str, default: int) -> int:
        tokens = (message_str or "").split()
        if tokens and tokens[-1].isdigit():
            return max(0, int(tokens[-1]))
        return default

    def _resolve_backup_root(self) -> Path | None:
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                f"[repeat_delete] 无法定位插件数据目录，历史备份已关闭: {exc}"
            )
            return None
        return data_dir / "backup"


def _format_report(
    settings: PluginSettings,
    report: TidyReport,
    *,
    preview: bool,
    elapsed: float,
    backup_path: Path | None,
) -> str:
    title = "🔍 think 整理预览（未写入）" if preview else "🧹 think 整理完成"
    short_id = (report.conv_id or "unknown")[:8]
    lines = [
        title,
        f"对话 {short_id} ｜ 消息 {report.messages} 条（扫描 {report.scanned_messages}）",
    ]

    if report.think_blocks == 0:
        lines.append(
            "没有可整理的 think 内容：模型未返回思考，或该会话历史里没有 think 部分。"
        )
        return "\n".join(lines)

    if not report.changed:
        lines.append(
            f"think 块 {report.think_blocks} 个，共 {report.before} 字，已经是干净的了。"
        )
        return "\n".join(lines)

    lines.append(
        f"think 块 {report.think_blocks} 个 ｜ 改写 {report.changed_blocks} 个"
        + (f" ｜ 移除空块 {report.removed_parts} 个" if report.removed_parts else "")
    )
    lines.append(
        f"字数 {report.before} → {report.after}"
        f"（-{report.saved_chars}，{report.saved_ratio * 100:.1f}%）"
    )
    detail = (
        f"关键词 {report.keyword_hits} 处 ｜ 删段 {report.dropped_paragraphs}"
        f" ｜ 删行 {report.dropped_lines}"
    )
    if settings.clean.dedupe_sentences and report.dropped_sentences:
        detail += f" ｜ 删句 {report.dropped_sentences}"
    lines.append(detail)

    if report.skipped_large_blocks:
        lines.append(
            f"跳过超长块 {report.skipped_large_blocks} 个"
            f"（max_block_chars={settings.max_block_chars}）"
        )
    if report.cleared_signatures:
        lines.append(
            f"清理思考签名 {report.cleared_signatures} 个（内容已改动，原签名失效）"
        )

    if report.top_blocks:
        lines.append("删除最多的块：")
        for block in report.top_blocks:
            preview_text = block.preview or "…"
            lines.append(
                f"  #{block.message_index} {block.before} → {block.after}"
                f"（-{block.before - block.after}）{preview_text}"
            )

    if preview:
        lines.append("这是预览结果，发送 /think tidy 才会真正写回。")
    elif backup_path is not None:
        lines.append(f"备份：{_short_path(backup_path)}")

    lines.append(f"耗时 {elapsed:.2f}s")
    return "\n".join(lines)


def _short_path(path: Path) -> str:
    text = str(path)
    return text if len(text) <= 90 else "…" + text[-88:]


@asynccontextmanager
async def _session_guard(umo: str, logger: Any = None):
    """尽量与同会话的 LLM 请求串行，避免写回被随后的历史保存覆盖。"""
    if session_lock_manager is None:
        yield
        return

    guard = session_lock_manager.acquire_lock(umo)
    entered = False
    try:
        await asyncio.wait_for(guard.__aenter__(), SESSION_LOCK_TIMEOUT)
        entered = True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning(
                f"[repeat_delete] 等待会话锁超时/失败（{exc}），将在未加锁的情况下写入"
            )
    try:
        yield
    finally:
        if entered:
            await guard.__aexit__(None, None, None)
