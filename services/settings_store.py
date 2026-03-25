from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from config import DEFAULT_LEAD_MINUTES, SCHEDULER_INTERVAL_SECONDS, get_haile_token, get_pushplus_url
from services.db import database
from services.haier_client import HaierClient


MIN_POLL_INTERVAL_SECONDS = 5
MAX_POLL_INTERVAL_SECONDS = 3600


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def clamp_int(value: Any, fallback: int, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(number, minimum)


@dataclass
class EffectiveSettings:
    token: str
    pushplus_url: str
    default_lead_minutes: int
    reservation_poll_interval_seconds: int
    sources: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'token': self.token,
            'pushplusUrl': self.pushplus_url,
            'defaultLeadMinutes': self.default_lead_minutes,
            'reservationPollIntervalSeconds': self.reservation_poll_interval_seconds,
            'sources': self.sources,
        }


class SettingsStore:
    editable_keys = {
        'token',
        'pushplus_url',
        'default_lead_minutes',
        'reservation_poll_interval_seconds',
    }

    def __init__(self) -> None:
        database.init()

    def _env_defaults(self) -> Dict[str, Any]:
        return {
            'token': get_haile_token(),
            'pushplus_url': get_pushplus_url(),
            'default_lead_minutes': DEFAULT_LEAD_MINUTES,
            'reservation_poll_interval_seconds': SCHEDULER_INTERVAL_SECONDS,
        }

    def _db_values(self) -> Dict[str, str]:
        rows = database.fetch_all('SELECT key, value FROM app_settings')
        return {str(row['key']): str(row['value'] or '') for row in rows}

    def get_effective_settings(self) -> EffectiveSettings:
        defaults = self._env_defaults()
        db_values = self._db_values()

        token = db_values.get('token', defaults['token']).strip()
        pushplus_url = db_values.get('pushplus_url', defaults['pushplus_url']).strip()
        default_lead_minutes = clamp_int(
            db_values.get('default_lead_minutes'),
            int(defaults['default_lead_minutes']),
            1,
        )
        reservation_poll_interval_seconds = clamp_int(
            db_values.get('reservation_poll_interval_seconds'),
            int(defaults['reservation_poll_interval_seconds']),
            MIN_POLL_INTERVAL_SECONDS,
        )

        sources = {
            'token': 'database' if 'token' in db_values else 'env',
            'pushplusUrl': 'database' if 'pushplus_url' in db_values else 'env',
            'defaultLeadMinutes': 'database' if 'default_lead_minutes' in db_values else 'env',
            'reservationPollIntervalSeconds': 'database' if 'reservation_poll_interval_seconds' in db_values else 'env',
        }
        return EffectiveSettings(
            token=token,
            pushplus_url=pushplus_url,
            default_lead_minutes=default_lead_minutes,
            reservation_poll_interval_seconds=min(reservation_poll_interval_seconds, MAX_POLL_INTERVAL_SECONDS),
            sources=sources,
        )

    def update_settings(self, payload: Dict[str, Any]) -> EffectiveSettings:
        updates: list[tuple[str, str | None]] = []
        if 'token' in payload:
            token = str(payload.get('token') or '').strip()
            updates.append(('token', token or None))
        if 'pushplusUrl' in payload:
            pushplus_url = str(payload.get('pushplusUrl') or '').strip()
            updates.append(('pushplus_url', pushplus_url or None))
        if 'defaultLeadMinutes' in payload:
            try:
                lead_minutes = int(payload.get('defaultLeadMinutes'))
            except (TypeError, ValueError):
                raise ValueError('默认提前建单分钟数必须是正整数')
            if lead_minutes <= 0:
                raise ValueError('默认提前建单分钟数必须大于 0')
            updates.append(('default_lead_minutes', str(lead_minutes)))
        if 'reservationPollIntervalSeconds' in payload:
            try:
                poll_interval = int(payload.get('reservationPollIntervalSeconds'))
            except (TypeError, ValueError):
                raise ValueError('预约轮询间隔必须是整数秒')
            if poll_interval < MIN_POLL_INTERVAL_SECONDS or poll_interval > MAX_POLL_INTERVAL_SECONDS:
                raise ValueError(f'预约轮询间隔必须在 {MIN_POLL_INTERVAL_SECONDS}-{MAX_POLL_INTERVAL_SECONDS} 秒之间')
            updates.append(('reservation_poll_interval_seconds', str(poll_interval)))

        timestamp = now_iso()
        for key, value in updates:
            if value is None:
                database.execute('DELETE FROM app_settings WHERE key = ?', (key,))
                continue
            database.execute(
                '''
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                ''',
                (key, value, timestamp),
            )

        return self.get_effective_settings()

    def validate_token(self, token: str | None = None) -> Dict[str, Any]:
        settings = self.get_effective_settings()
        effective_token = (token if token is not None else settings.token).strip()
        source = 'request' if token is not None else settings.sources['token']
        if not effective_token:
            return {
                'source': source,
                'configured': False,
                'valid': False,
                'reason': 'missing',
                'message': '当前未配置可用 Token，请先在设置页或 .env 中填写。',
            }

        client = HaierClient(effective_token)
        result = client.get_underway_orders()
        if result.get('ok'):
            return {
                'source': source,
                'configured': True,
                'valid': True,
                'reason': 'ok',
                'message': 'Token 校验通过。',
            }

        if result.get('error_type') == 'business':
            return {
                'source': source,
                'configured': True,
                'valid': False,
                'reason': 'invalid',
                'message': 'Token 无效或已失效，请更新后重试。',
            }

        return {
            'source': source,
            'configured': True,
            'valid': False,
            'reason': 'check_failed',
            'message': f"暂时无法校验 Token：{result.get('msg') or '请稍后重试。'}",
        }


settings_store = SettingsStore()
