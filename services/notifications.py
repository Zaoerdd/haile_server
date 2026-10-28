from __future__ import annotations

import time
from typing import Any, Dict

import requests

from config import PUSHPLUS_API_URL, PUSHPLUS_TIMEOUT

RATE_LIMIT_CODE = 999
RATE_LIMIT_COOLDOWN_SECONDS = 90


def is_http_url(value: str) -> bool:
    text = str(value or '').strip().lower()
    return text.startswith('http://') or text.startswith('https://')


def normalize_target(value: str) -> str:
    """清洗用户粘贴进来的 PushPlus 目标。

    手机端粘贴 token 常常带首尾空格或换行，纯 token 里不允许空白，
    所以非 URL 一律把内部空白全部去掉；URL 只做首尾 trim（查询串可能含编码内容）。
    """
    text = str(value or '').strip()
    if not text:
        return ''
    if is_http_url(text):
        return text
    return ''.join(text.split())


def describe_target(value: str) -> str:
    """给前端展示用的脱敏描述。"""
    text = normalize_target(value)
    if not text:
        return ''
    if is_http_url(text):
        return text
    if len(text) <= 8:
        return text
    return f'{text[:4]}****{text[-4:]}'


class PushPlusNotifier:
    """PushPlus 推送。

    target 支持两种形式：
    - 完整 URL（自建网关 / 带参数的推送地址），直接 POST
    - 纯 token（32 位十六进制），走官方 https://www.pushplus.plus/send

    PushPlus 对短时间连续推送会返回 code=999「服务端验证错误」（实为频率限制），
    因此命中后进入冷却期，避免每个轮询周期都去撞一次。
    """

    def __init__(self) -> None:
        self._blocked_until = 0.0

    def notify(
        self,
        target: str,
        title: str,
        content: str,
        *,
        template: str = 'html',
    ) -> Dict[str, Any]:
        raw_target = normalize_target(target)
        if not raw_target:
            return {
                'sent': False,
                'reason': 'missing_pushplus_target',
                'message': '未配置 PushPlus token 或链接，本次通知仅记录事件。',
            }

        if time.time() < self._blocked_until:
            remain = int(self._blocked_until - time.time())
            return {
                'sent': False,
                'reason': 'rate_limited',
                'message': f'PushPlus 冷却中（约 {remain} 秒），本次跳过。',
            }

        if is_http_url(raw_target):
            url = raw_target
            payload: Dict[str, Any] = {'title': title, 'content': content, 'template': template}
        else:
            url = PUSHPLUS_API_URL
            payload = {'token': raw_target, 'title': title, 'content': content, 'template': template}

        try:
            response = requests.post(url, json=payload, timeout=PUSHPLUS_TIMEOUT)
        except requests.RequestException as exc:
            return {
                'sent': False,
                'reason': 'request_failed',
                'message': f'PushPlus 请求失败：{exc}',
            }

        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict):
            code = body.get('code')
            message = str(body.get('msg') or '')
        else:
            code = None
            message = (response.text or '').strip()[:200]

        if response.status_code == 200 and code in (200, '200'):
            return {'sent': True, 'code': code, 'message': message or '推送成功'}

        if code in (RATE_LIMIT_CODE, str(RATE_LIMIT_CODE)):
            self._blocked_until = time.time() + RATE_LIMIT_COOLDOWN_SECONDS
            return {
                'sent': False,
                'reason': 'rate_limited',
                'code': code,
                'message': 'PushPlus 触发频率限制，已进入冷却，稍后自动重试。',
            }

        return {
            'sent': False,
            'reason': 'pushplus_rejected',
            'code': code if code is not None else response.status_code,
            'message': message or f'PushPlus 返回 HTTP {response.status_code}',
        }


pushplus_notifier = PushPlusNotifier()
