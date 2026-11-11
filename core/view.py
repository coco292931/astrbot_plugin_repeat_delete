"""``/think view`` 的排版与发送辅助。

合并转发依赖 AstrBot 的 ``Node`` / ``Nodes`` 组件：aiocqhttp 与 satori 适配器会把它们
转成平台原生的转发消息，其它平台没有对应概念，退化成多条纯文本。
这里不截断内容，只在单个转发节点过长时切成多个节点。
"""

from __future__ import annotations

from typing import Any

from .history import ViewResult

DEFAULT_FORWARD_PLATFORMS: tuple[str, ...] = ("aiocqhttp", "satori")


def supports_forward(
    platform_name: str, platforms: tuple[str, ...] = DEFAULT_FORWARD_PLATFORMS
) -> bool:
    """当前平台是否支持合并转发。``platforms`` 为空表示不限制。"""
    if not platforms:
        return True
    name = (platform_name or "").strip().lower()
    return name in {item.strip().lower() for item in platforms if item.strip()}


def scope_label(count: int) -> str:
    return f"最近 {count} 条消息" if count > 0 else "全部消息"


def summary_text(view: ViewResult, *, count: int) -> str:
    """转发消息的第一个节点 / 纯文本开头的摘要。"""
    lines = [
        "💭 think 查看",
        f"对话 {(view.conv_id or 'unknown')[:8]} ｜ {scope_label(count)}"
        f"（扫描 {view.scanned_messages}/{view.messages} 条）",
        f"think 块 {len(view.blocks)} 个 ｜ 合计 {view.total_chars} 字",
    ]
    if view.truncated_blocks:
        lines.append(f"其中 {view.truncated_blocks} 个块按配置截断显示")
    return "\n".join(lines)


def block_nodes(view: ViewResult, *, node_name: str, node_max_chars: int) -> list[str]:
    """把每个 think 块渲染成「一个或多个」节点正文。"""
    bodies: list[str] = []
    for block in view.blocks:
        segments = split_text(block.text, node_max_chars)
        for order, segment in enumerate(segments):
            header = block.header if order == 0 else f"{block.header}（续 {order + 1}）"
            bodies.append(f"{header}\n{segment}")
    return bodies


def forward_components(
    view: ViewResult,
    *,
    count: int,
    node_name: str = "think",
    node_max_chars: int = 0,
) -> list[Any]:
    """构造合并转发的消息链组件；组件不可用时返回空列表。

    所有节点必须装进**一个** ``Nodes``：aiocqhttp 适配器会把消息链里每个顶层
    ``Node`` / ``Nodes`` 分别发成一条转发消息，散着放会变成一堆单节点转发。
    """
    try:
        from astrbot.api.message_components import Node, Nodes, Plain
    except Exception:  # pragma: no cover - 老版本 AstrBot
        return []

    try:
        nodes = [
            Node(
                uin=0,
                name=node_name,
                content=[Plain(text=summary_text(view, count=count))],
            )
        ]
        for body in block_nodes(
            view, node_name=node_name, node_max_chars=node_max_chars
        ):
            nodes.append(Node(uin=0, name=node_name, content=[Plain(text=body)]))
        return [Nodes(nodes)]
    except Exception:  # pragma: no cover - 组件签名变化
        return []


def plain_chunks(view: ViewResult, *, count: int, chunk_chars: int) -> list[str]:
    """不支持的平台上的回退：整段文本按长度切分。"""
    parts: list[str] = [summary_text(view, count=count), ""]
    for block in view.blocks:
        parts.append(block.header)
        parts.append(block.text)
        parts.append("")
    full = "\n".join(parts).strip()
    return split_text(full, chunk_chars)


def split_text(text: str, limit: int) -> list[str]:
    """按长度切分，优先在换行处断开；``limit <= 0`` 表示不切分。"""
    if not text:
        return [""]
    if limit <= 0 or len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in text.split("\n"):
        # 单行本身就超长时先按长度硬切
        while len(line) > limit:
            head, line = line[:limit], line[limit:]
            if buffer:
                chunks.append("\n".join(buffer))
                buffer, size = [], 0
            chunks.append(head)
        extra = len(line) + (1 if buffer else 0)
        if size + extra > limit and buffer:
            chunks.append("\n".join(buffer))
            buffer, size = [], 0
            extra = len(line)
        buffer.append(line)
        size += extra
    if buffer:
        chunks.append("\n".join(buffer))
    return [chunk for chunk in chunks if chunk]
