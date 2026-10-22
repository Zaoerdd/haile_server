from __future__ import annotations

import atexit
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import ORDER_WATCH_INTERVAL_SECONDS, PUSHPLUS_ENDING_REMIND_MINUTES
from services.db import database
from services.haier_client import HaierClient
from services.notifications import pushplus_notifier
from services.reservation_service import now_local, parse_iso
from services.settings_store import settings_store


# 订单进入这些状态说明机器已经真的开始工作
# 注意：洗衣机实际返回的是「洗衣中」，不是「洗涤中」；两者都要覆盖
RUNNING_KEYWORDS = ('运行', '洗衣中', '洗涤中', '洗中', '脱水中', '烘干中', '已启动', '使用中', '预计')
# 这些状态说明订单还没真正开始（待付款 / 待确认等）
PENDING_KEYWORDS = ('待付款', '待支付', '待确认', '待验证', '待放衣', '待启动')
# 这些状态说明订单已经结束
FINISHED_KEYWORDS = ('已完成', '已结束', '已关闭', '已取消', '已失效', '交易完成')

MISSING_GRACE = timedelta(minutes=10)
RETENTION = timedelta(days=7)


def to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def is_running_state(state_desc: str) -> bool:
    text = str(state_desc or '')
    if not text:
        return False
    if any(keyword in text for keyword in PENDING_KEYWORDS):
        return False
    return any(keyword in text for keyword in RUNNING_KEYWORDS)


def is_finished_state(state_desc: str) -> bool:
    text = str(state_desc or '')
    return any(keyword in text for keyword in FINISHED_KEYWORDS)


def extract_finish_time(detail: Dict[str, Any]) -> Optional[datetime]:
    payload = detail if isinstance(detail, dict) else {}
    order_item = (payload.get('orderItemList') or [{}])[0]
    fulfil_info = payload.get('fulfillInfo') or {}
    fulfilling_item = fulfil_info.get('fulfillingItem') or {}
    raw = (
        fulfilling_item.get('finishTime')
        or order_item.get('finishTime')
        or payload.get('finishTime')
    )
    return parse_iso(raw) if raw else None


def format_time(value: Optional[datetime]) -> str:
    return value.strftime('%H:%M') if isinstance(value, datetime) else '未知'


def build_message(title: str, lines: list[str]) -> str:
    body = ''.join(f'<p>{line}</p>' for line in lines if line)
    return f'<h3>{title}</h3>{body}'


class OrderWatchService:
    """盯住进行中订单，推送「开始 / 结束前 N 分钟 / 结束」三类通知。"""

    def __init__(self) -> None:
        database.init()

    def _settings(self):
        return settings_store.get_effective_settings()

    def _notify(self, title: str, lines: list[str]) -> Dict[str, Any]:
        target = self._settings().pushplus_url
        return pushplus_notifier.notify(target, title, build_message(title, lines))

    def _upsert(self, order_no: str, machine: str, state_desc: str, now: datetime) -> Dict[str, Any]:
        row = database.fetch_one('SELECT * FROM order_watch WHERE order_no = ?', (order_no,))
        if row is None:
            database.execute(
                '''
                INSERT INTO order_watch(order_no, machine_name, state_desc, finish_time, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, ?)
                ''',
                (order_no, machine, state_desc, to_iso(now), to_iso(now)),
            )
            return {
                'start_notified': 0,
                'ending_notified': 0,
                'finished_notified': 0,
                'finish_time': None,
                'missing_since': None,
            }
        database.execute(
            'UPDATE order_watch SET state_desc = ?, updated_at = ?, missing_since = NULL WHERE order_no = ?',
            (state_desc, to_iso(now), order_no),
        )
        return {
            'start_notified': int(row['start_notified'] or 0),
            'ending_notified': int(row['ending_notified'] or 0),
            'finished_notified': int(row['finished_notified'] or 0),
            'finish_time': row['finish_time'],
            'missing_since': row['missing_since'],
        }

    def _mark(self, order_no: str, column: str) -> None:
        if column not in {'start_notified', 'ending_notified', 'finished_notified'}:
            return
        database.execute(
            f'UPDATE order_watch SET {column} = 1, updated_at = ? WHERE order_no = ?',
            (to_iso(now_local()), order_no),
        )

    def _save_finish_time(self, order_no: str, finish: datetime) -> None:
        database.execute(
            'UPDATE order_watch SET finish_time = ?, updated_at = ? WHERE order_no = ?',
            (to_iso(finish), to_iso(now_local()), order_no),
        )

    def tick(self) -> Dict[str, Any]:
        try:
            token = str(self._settings().token or '').strip()
        except Exception as exc:  # noqa: BLE001
            return {'error': f'settings_failed: {exc}'}
        if not token:
            return {'skipped': 'missing_token'}

        client = HaierClient(token)
        orders_res = client.get_underway_orders()
        if not orders_res.get('ok'):
            return {'error': orders_res.get('msg') or '读取进行中订单失败'}

        now = now_local()
        remind_seconds = max(int(PUSHPLUS_ENDING_REMIND_MINUTES), 1) * 60
        report = {'checked': 0, 'started': 0, 'ending': 0, 'finished': 0, 'notifyFailed': 0}
        seen: set[str] = set()

        for order in orders_res.get('data') or []:
            item = order if isinstance(order, dict) else {}
            order_no = str(item.get('orderNo') or '').strip()
            if not order_no:
                continue
            seen.add(order_no)
            report['checked'] += 1

            state_desc = str(item.get('stateDesc') or '')
            machine = str(item.get('projectName') or item.get('goodsName') or '')
            flags = self._upsert(order_no, machine, state_desc, now)
            running = is_running_state(state_desc)

            if running and not flags['start_notified']:
                result = self._notify(
                    '洗衣已开始',
                    [f'机器：{machine or "未知"}', f'订单：{order_no}', f'状态：{state_desc}'],
                )
                if result.get('sent'):
                    report['started'] += 1
                    self._mark(order_no, 'start_notified')
                else:
                    report['notifyFailed'] += 1

            # 状态里已经出现「完成 / 结束 / 关闭」时直接判定结束，不必等结束时间
            if not flags['finished_notified'] and flags['start_notified'] and is_finished_state(state_desc):
                result = self._notify(
                    '洗衣已结束',
                    [f'机器：{machine or "未知"}', f'订单：{order_no}', f'状态：{state_desc}'],
                )
                if result.get('sent'):
                    report['finished'] += 1
                    self._mark(order_no, 'finished_notified')
                else:
                    report['notifyFailed'] += 1
                continue

            finish = parse_iso(flags['finish_time']) if flags['finish_time'] else None
            should_refresh = finish is None or (finish - now).total_seconds() <= remind_seconds + 60
            if should_refresh and (running or flags['start_notified']):
                detail_res = client.order_detail(order_no, settle=False)
                if detail_res.get('ok'):
                    fresh_finish = extract_finish_time(detail_res.get('data') or {})
                    if fresh_finish is not None and fresh_finish != finish:
                        finish = fresh_finish
                        self._save_finish_time(order_no, finish)

            if finish is None or flags['finished_notified']:
                continue

            remaining = (finish - now).total_seconds()
            if remaining <= 0:
                if not flags['start_notified']:
                    # 从未开始洗的订单（例如待付款后取消），不推「结束」避免误导
                    self._mark(order_no, 'finished_notified')
                    continue
                result = self._notify(
                    '洗衣已结束',
                    [f'机器：{machine or "未知"}', f'订单：{order_no}', f'结束时间：{format_time(finish)}'],
                )
                if result.get('sent'):
                    report['finished'] += 1
                    self._mark(order_no, 'finished_notified')
                else:
                    report['notifyFailed'] += 1
                continue

            minutes = max(int(round(remaining / 60)), 1)
            if remaining <= remind_seconds and not flags['ending_notified']:
                result = self._notify(
                    f'还有 {minutes} 分钟结束',
                    [f'机器：{machine or "未知"}', f'订单：{order_no}', f'预计结束：{format_time(finish)}'],
                )
                if result.get('sent'):
                    report['ending'] += 1
                    self._mark(order_no, 'ending_notified')
                else:
                    report['notifyFailed'] += 1

        report['missing'] = self._handle_missing(seen, now, report)
        self._cleanup(now)
        return report

    def _handle_missing(self, seen: set[str], now: datetime, report: Dict[str, Any]) -> int:
        handled = 0
        rows = database.fetch_all('SELECT * FROM order_watch WHERE finished_notified = 0')
        for row in rows:
            order_no = str(row['order_no'])
            if order_no in seen:
                continue
            finish = parse_iso(row['finish_time']) if row['finish_time'] else None
            should_finish = bool(finish and now >= finish)

            missing_since = parse_iso(row['missing_since']) if row['missing_since'] else None
            if missing_since is None:
                database.execute(
                    'UPDATE order_watch SET missing_since = ?, updated_at = ? WHERE order_no = ?',
                    (to_iso(now), to_iso(now), order_no),
                )
            if not should_finish and missing_since is not None:
                should_finish = (now - missing_since) >= MISSING_GRACE

            if not should_finish:
                continue

            if not int(row['start_notified'] or 0):
                # 只是待付款订单被取消，静默归档
                self._mark(order_no, 'finished_notified')
                continue

            machine = str(row['machine_name'] or '未知')
            lines = [f'机器：{machine}', f'订单：{order_no}', f'结束时间：{format_time(finish)}']
            result = self._notify('洗衣已结束', lines)
            if result.get('sent'):
                report['finished'] += 1
                self._mark(order_no, 'finished_notified')
            else:
                report['notifyFailed'] += 1
            handled += 1
        return handled

    def _cleanup(self, now: datetime) -> None:
        cutoff = now - RETENTION
        database.execute('DELETE FROM order_watch WHERE updated_at < ?', (to_iso(cutoff),))


order_watch_service = OrderWatchService()


class OrderWatchScheduler:
    def __init__(self, interval_seconds: int = ORDER_WATCH_INTERVAL_SECONDS) -> None:
        self.interval_seconds = max(int(interval_seconds), 10)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self.last_tick_at: str | None = None
        self.last_result: Dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name='order-watch', daemon=True)
            self._thread.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def snapshot(self) -> Dict[str, Any]:
        return {
            'running': self._running,
            'intervalSeconds': self.interval_seconds,
            'lastTickAt': self.last_tick_at,
            'lastResult': self.last_result,
            'lastError': self.last_error,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.last_tick_at = to_iso(now_local())
            try:
                self.last_result = order_watch_service.tick()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            self._stop_event.wait(self.interval_seconds)


order_watch_scheduler = OrderWatchScheduler()
atexit.register(order_watch_scheduler.stop)
