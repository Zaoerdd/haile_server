from __future__ import annotations

from typing import Any, Dict


class PushPlusNotifier:
    def notify(self, pushplus_url: str, title: str, content: str) -> Dict[str, Any]:
        if not pushplus_url:
            return {
                'sent': False,
                'reason': 'missing_pushplus_url',
                'message': '未配置 PushPlus 链接，本次通知仅记录事件。',
            }

        return {
            'sent': False,
            'reason': 'todo',
            'message': 'PushPlus 推送占位已保留，后续补充发送实现。',
            'title': title,
            'content': content,
        }


pushplus_notifier = PushPlusNotifier()
