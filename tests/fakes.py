"""最小化的 AstrBot API 替身，供本地测试导入插件入口。"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeEvent:
    def __init__(self, umo: str, message_str: str, role: str = "admin") -> None:
        self.unified_msg_origin = umo
        self.message_str = message_str
        self.role = role
        self.stopped = False

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult(text)

    def is_admin(self) -> bool:
        return self.role == "admin"

    def stop_event(self) -> None:
        self.stopped = True


class FakeConversation:
    def __init__(self, history: str) -> None:
        self.history = history


class FakeConversationManager:
    def __init__(self, history: list, conv_id: str = "abcdef1234567890") -> None:
        import json

        self.conv_id = conv_id
        self.history = json.dumps(history, ensure_ascii=False)
        self.saved: list | None = None
        self.save_calls = 0

    async def get_curr_conversation_id(self, umo: str) -> str:
        return self.conv_id

    async def get_conversation(self, umo: str, cid: str) -> FakeConversation:
        return FakeConversation(self.history)

    async def update_conversation(
        self, umo: str, conversation_id: str | None = None, history=None, **kwargs
    ) -> None:
        self.saved = history
        self.save_calls += 1
        import json

        self.history = json.dumps(history, ensure_ascii=False)


class FakeContext:
    def __init__(self, manager) -> None:
        self.conversation_manager = manager


def install_astrbot_stubs(data_dir: Path) -> None:
    """注册伪造的 astrbot 模块，使插件入口可以脱离 AstrBot 运行时导入。"""
    if "astrbot.api.event" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")

    class AstrMessageEvent:
        pass

    class _RegisteringCommandable:
        """模拟 AstrBot 的 RegisteringCommandable，只记录注册的指令。"""

        def __init__(self, group_name, alias=None) -> None:
            self.group_name = group_name
            self.alias = alias or set()
            self.sub_commands: list[str] = []

        def command(self, command_name, alias=None, **kwargs):
            self.sub_commands.append(command_name)

            def decorator(func):
                return func

            return decorator

    class _Filter:
        def __init__(self) -> None:
            self.groups: dict[str, _RegisteringCommandable] = {}

        def command_group(self, name, alias=None, **kwargs):
            group = _RegisteringCommandable(name, alias)
            self.groups[name] = group

            def decorator(func=None):
                return group

            return decorator

        def command(self, name, alias=None, **kwargs):
            def decorator(func):
                return func

            return decorator

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = _Filter()

    class Star:
        def __init__(self, context, config=None) -> None:
            self.context = context
            self.logger = logging.getLogger("repeat_delete_test")

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name=None):
            target = data_dir / str(plugin_name or "plugin")
            target.mkdir(parents=True, exist_ok=True)
            return target

    def register(*args, **kwargs):
        return lambda cls: cls

    star_mod.Context = object
    star_mod.Star = Star
    star_mod.StarTools = StarTools
    star_mod.register = register

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
