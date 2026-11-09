"""插件入口与历史改写流程的测试。

不依赖 AstrBot 运行时：用 tests/fakes.py 里的最小替身注册 astrbot 模块。
运行：python tests/test_plugin.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(PLUGIN_ROOT))

from tests.fakes import (  # noqa: E402
    FakeContext,
    FakeConversationManager,
    FakeEvent,
    install_astrbot_stubs,
)

DATA_DIR = Path(tempfile.mkdtemp(prefix="repeat_delete_test_"))
install_astrbot_stubs(DATA_DIR)

import importlib  # noqa: E402

plugin_main = importlib.import_module(f"{PLUGIN_ROOT.name}.main")
RepeatDeletePlugin = plugin_main.RepeatDeletePlugin
history_mod = importlib.import_module(f"{PLUGIN_ROOT.name}.core.history")
load_settings = importlib.import_module(f"{PLUGIN_ROOT.name}.core.config").load_settings

UMO = "aiocqhttp:FriendMessage:12345"

NOISY_THINK = (
    "我想想该怎么回答她。\n\n"
    "好\n好了\n输出\n\n"
    "好\n好了\n输出\n\n"
    "好\n好了\n输出\n\n"
    "她问的是天气，应该先确认城市。"
)


def build_history(think: str = NOISY_THINK) -> list:
    return [
        {"role": "user", "content": "今天天气怎么样"},
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": think, "encrypted": "sig-abc"},
                {"type": "text", "text": "告诉我城市名我再查"},
            ],
        },
    ]


def run(plugin, event, preview=False):
    async def _collect():
        return [item async for item in plugin._handle(event, preview)]

    return asyncio.run(_collect())


def make_plugin(config: dict, manager) -> RepeatDeletePlugin:
    plugin = RepeatDeletePlugin(FakeContext(manager), config)
    plugin._backup_root = DATA_DIR / "backup"
    return plugin


def test_tidy_rewrites_history():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({}, manager)
    event = FakeEvent(UMO, "think tidy")

    results = run(plugin, event, preview=False)

    # 终止事件会让 AstrBot 跳过 Respond 阶段，回复就发不出去了
    assert event.stopped is False
    assert manager.save_calls == 1
    saved = manager.saved
    assert saved is not None
    think = saved[1]["content"][0]
    assert think["think"] == "我想想该怎么回答她。\n\n她问的是天气，应该先确认城市。"
    assert think["encrypted"] is None
    assert saved[1]["content"][1] == {"type": "text", "text": "告诉我城市名我再查"}
    assert saved[0] == {"role": "user", "content": "今天天气怎么样"}
    assert "think 整理完成" in results[0].text


def test_preview_does_not_write():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({}, manager)
    results = run(plugin, FakeEvent(UMO, "think preview"), preview=True)

    assert manager.save_calls == 0
    assert "预览" in results[0].text


def test_dry_run_config_forces_preview():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({"dry_run": True}, manager)
    results = run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert manager.save_calls == 0
    assert "预览" in results[0].text


def test_backup_written_before_save():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({}, manager)
    run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    files = list((DATA_DIR / "backup").rglob("*.json"))
    assert files, "应写入备份文件"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload[1]["content"][0]["think"] == NOISY_THINK


def test_clean_history_reports_nothing_to_do():
    clean = "想清楚了，直接问她城市。"
    manager = FakeConversationManager(build_history(clean))
    plugin = make_plugin({}, manager)
    results = run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert manager.save_calls == 0
    assert "已经是干净的了" in results[0].text


def test_no_think_content():
    manager = FakeConversationManager([{"role": "user", "content": "hi"}])
    plugin = make_plugin({}, manager)
    results = run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert "没有可整理的 think 内容" in results[0].text


def test_disabled_plugin_blocks():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({"enabled": False}, manager)
    results = run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert manager.save_calls == 0
    assert "关闭" in results[0].text


def test_admin_only_blocks_member():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin({"admin_only": True}, manager)
    results = run(plugin, FakeEvent(UMO, "think tidy", role="member"), preview=False)

    assert manager.save_calls == 0
    assert "管理员" in results[0].text


def test_whitelist_blocks_other_session():
    manager = FakeConversationManager(build_history())
    plugin = make_plugin(
        {"session_mode": "whitelist", "sessions": ["telegram:Group:1"]}, manager
    )
    results = run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert manager.save_calls == 0
    assert "白名单" in results[0].text


def test_trailing_number_limits_scope():
    history = [
        {
            "role": "assistant",
            "content": [{"type": "think", "think": NOISY_THINK}],
        },
        {
            "role": "assistant",
            "content": [{"type": "think", "think": NOISY_THINK}],
        },
    ]
    manager = FakeConversationManager(history)
    plugin = make_plugin({}, manager)
    run(plugin, FakeEvent(UMO, "think tidy 1"), preview=False)

    saved = manager.saved
    assert isinstance(saved, list)
    assert saved[0]["content"][0]["think"] == NOISY_THINK, "只应处理最后一条"
    assert saved[1]["content"][0]["think"] != NOISY_THINK


def test_empty_think_part_removed_but_part_list_not_empty():
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "好\n好了\n输出"},
                {"type": "text", "text": "收到"},
            ],
        }
    ]
    manager = FakeConversationManager(history)
    plugin = make_plugin({}, manager)
    run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    content = manager.saved[0]["content"]
    assert content == [{"type": "text", "text": "收到"}]


def test_empty_think_part_kept_when_only_part():
    history = [
        {"role": "assistant", "content": [{"type": "think", "think": "好\n好了"}]}
    ]
    manager = FakeConversationManager(history)
    plugin = make_plugin({}, manager)
    run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    content = manager.saved[0]["content"]
    assert content == [{"type": "think", "think": ""}]


def test_original_history_object_not_mutated():
    history = build_history()
    cleaner = plugin_main.ThinkCleaner(load_settings({}).clean)
    new_history, report = history_mod.tidy_messages(
        history,
        cleaner,
        roles=("assistant",),
    )

    assert report.changed is True
    assert history[1]["content"][0]["think"] == NOISY_THINK
    assert new_history is not history
    assert new_history[1]["content"][0]["think"] != NOISY_THINK


def test_mixed_history_only_think_parts_change():
    cleaner = plugin_main.ThinkCleaner(load_settings({}).clean)
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "好\n好\n好\n想一下"},
                {"type": "text", "text": "a"},
            ],
            "tool_calls": [{"id": "1"}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "result"},
        {"role": "_checkpoint", "content": {"id": "abc"}},
        {"role": "assistant", "content": "纯文本回复，不动它"},
    ]

    new_history, report = history_mod.tidy_messages(
        history, cleaner, roles=("assistant",)
    )

    assert len(new_history) == len(history)
    for index in (0, 1, 3, 4, 5):
        assert new_history[index] == history[index]
    assert new_history[2]["tool_calls"] == [{"id": "1"}]
    assert new_history[2]["content"] == [
        {"type": "think", "think": "想一下"},
        {"type": "text", "text": "a"},
    ]
    assert report.changed is True


def test_role_matching_is_case_insensitive():
    cleaner = plugin_main.ThinkCleaner(load_settings({}).clean)
    history = [
        {
            "role": "ASSISTANT",
            "content": [{"type": "think", "think": "好了\n好了\n真正的想法"}],
        }
    ]
    new_history, report = history_mod.tidy_messages(
        history, cleaner, roles=("Assistant",)
    )
    assert report.think_blocks == 1
    assert new_history[0]["content"][0]["think"] == "真正的想法"


def test_pre_existing_empty_think_part_is_left_alone():
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "", "encrypted": "sig"},
                {"type": "text", "text": "正常回复"},
            ],
        }
    ]
    manager = FakeConversationManager(history)
    plugin = make_plugin({}, manager)
    run(plugin, FakeEvent(UMO, "think tidy"), preview=False)

    assert manager.save_calls == 0
    assert manager.saved is None


def test_parse_history_variants():
    assert history_mod.parse_history(None) == []
    assert history_mod.parse_history("[]") == []
    assert history_mod.parse_history([{"role": "user"}]) == [{"role": "user"}]
    try:
        history_mod.parse_history("not json")
    except history_mod.HistoryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("非法 JSON 应抛 HistoryError")


def test_load_settings_defaults_and_coercion():
    defaults = load_settings({})
    assert defaults.enabled is True
    assert defaults.clean.max_paragraph_repeats == 2
    assert "避免句号结尾" in defaults.clean.keywords

    coerced = load_settings(
        {
            "enabled": "false",
            "max_paragraph_repeats": "3",
            "near_duplicate": {"threshold": "0.9", "max_chars": "60"},
            "keywords": "好\n好了，输出",
            "roles": "assistant",
            "session_mode": "WHITELIST",
        }
    )
    assert coerced.enabled is False
    assert coerced.clean.max_paragraph_repeats == 3
    assert abs(coerced.clean.near_dup_threshold - 0.9) < 1e-9
    assert coerced.clean.near_dup_max_chars == 60
    assert coerced.clean.keywords == ("好", "好了", "输出")
    assert coerced.roles == ("assistant",)
    assert coerced.session_mode == "whitelist"


def main() -> int:
    failures = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print("-" * 40)
    print("failed:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
