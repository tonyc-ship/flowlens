"""WeChat desktop platform knowledge."""

from .app import WECHAT_APP_NAME, WeChatDesktopApp
from .models import WeChatMessage, WeChatParsedCapture
from .parser import WeChatConversationParser

__all__ = [
    "WECHAT_APP_NAME",
    "WeChatDesktopApp",
    "WeChatMessage",
    "WeChatParsedCapture",
    "WeChatConversationParser",
]
