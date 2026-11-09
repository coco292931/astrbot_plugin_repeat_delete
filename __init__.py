"""插件包入口。

AstrBot 直接加载 main.py，这个文件只是让本地测试可以按包名导入。
"""

from .main import RepeatDeletePlugin

__all__ = ["RepeatDeletePlugin"]
