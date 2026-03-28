from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import DEFAULT_LEAD_MINUTES
from services.db import database
from services.haier_client import HaierClient
from services.notifications import pushplus_notifier
from services.settings_store import settings_store

ReservationStatus = Literal['scheduled', 'holding', 'paused', 'completed', 'failed', 'deleted']


ACTIVE_TASK_STATUSES = {'scheduled', 'holding', 'paused'}
RUNNABLE_TASK_STATUSES = {'scheduled', 'holding'}
PENDING_ORDER_STATES = {50}
CLOSED_ORDER_STATES = {401, 411}
RUNNING_ORDER_STATES = {500}
COMPLETED_ORDER_STATES = {1000}
MACHINE_IDENTIFIER_KEYS = {
    'goodsId',
    'goodsCode',
    'goodsNo',
    'goodsSn',
    'deviceId',
    'deviceCode',
    'deviceNo',
    'deviceSn',
    'qrCode',
    'qrNo',
    'n',
    'sn',
    'code',
}
NO_ADOPTABLE_PENDING_ORDER_MESSAGE = '没有找到可接手的最终待付款订单。'
HISTORY_ORDER_LOOKUP_PAGE_SIZE = 20
EARLY_RENEW_LEAD_SECONDS = 60
try:
    REMOTE_ORDER_TIMEZONE = ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError:
    REMOTE_ORDER_TIMEZONE = timezone(timedelta(hours=8))


def now_local() -> datetime:
    return datetime.now().astimezone()


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now_local().tzinfo)
    return parsed.astimezone()


def parse_remote_order_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=REMOTE_ORDER_TIMEZONE).astimezone()
    return parsed.astimezone()


def parse_time_of_day(value: str) -> tuple[int, int]:
    parts = value.split(':', 1)
    if len(parts) != 2:
        raise ValueError('时间格式必须为 HH:MM')
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError('时间格式必须为 HH:MM')
    return hour, minute


def normalize_timezone_name(value: Any) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        ZoneInfo(text)
    except ZoneInfoNotFoundError as exc:
        raise ValueError('时区无效，请刷新页面后重试') from exc
    return text


def resolve_timezone(timezone_name: str | None):
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            pass
    return now_local().tzinfo or timezone.utc


def next_weekly_target(weekday: int, time_of_day: str, reference: datetime | None = None, timezone_name: str | None = None) -> datetime:
    if weekday < 0 or weekday > 6:
        raise ValueError('每周预约的星期必须在 0-6 之间')
    hour, minute = parse_time_of_day(time_of_day)
    ref = (reference or now_local()).astimezone(resolve_timezone(timezone_name))
    candidate = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - candidate.weekday()) % 7
    candidate = candidate + timedelta(days=days_ahead)
    if candidate <= ref:
        candidate = candidate + timedelta(days=7)
    return candidate


def build_windows(target_time: datetime, lead_minutes: int) -> tuple[datetime, datetime]:
    start_at = target_time - timedelta(minutes=lead_minutes)
    hold_until = target_time + timedelta(minutes=10)
    return start_at, hold_until


@dataclass
class ReservationTask:
    id: int
    title: str
    machine_source: str
    machine_id: str
    machine_name: str
    room_id: str | None
    room_name: str | None
    qr_code: str | None
    mode_id: int
    mode_name: str
    schedule_type: str
    target_time: datetime
    weekday: int | None
    time_of_day: str | None
    timezone_name: str | None
    lead_minutes: int
    status: ReservationStatus
    active_order_no: str | None
    start_at: datetime | None
    hold_until: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
    current_order_snapshot: str | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> 'ReservationTask':
        return cls(
            id=int(row['id']),
            title=str(row['title']),
            machine_source=str(row['machine_source']),
            machine_id=str(row['machine_id']),
            machine_name=str(row['machine_name']),
            room_id=str(row['room_id']) if row['room_id'] is not None else None,
            room_name=str(row['room_name']) if row['room_name'] is not None else None,
            qr_code=str(row['qr_code']) if row['qr_code'] is not None else None,
            mode_id=int(row['mode_id']),
            mode_name=str(row['mode_name']),
            schedule_type=str(row['schedule_type']),
            target_time=parse_iso(row['target_time']) or now_local(),
            weekday=int(row['weekday']) if row['weekday'] is not None else None,
            time_of_day=str(row['time_of_day']) if row['time_of_day'] is not None else None,
            timezone_name=str(row['timezone_name']) if row['timezone_name'] is not None else None,
            lead_minutes=int(row['lead_minutes']),
            status=str(row['status']),
            active_order_no=str(row['active_order_no']) if row['active_order_no'] is not None else None,
            start_at=parse_iso(row['start_at']),
            hold_until=parse_iso(row['hold_until']),
            last_checked_at=parse_iso(row['last_checked_at']),
            last_error=str(row['last_error']) if row['last_error'] is not None else None,
            current_order_snapshot=str(row['current_order_snapshot']) if row['current_order_snapshot'] is not None else None,
            last_run_at=parse_iso(row['last_run_at']),
            created_at=parse_iso(row['created_at']) or now_local(),
            updated_at=parse_iso(row['updated_at']) or now_local(),
        )

    def to_dict(self, last_event: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'machineSource': self.machine_source,
            'machineId': self.machine_id,
            'machineName': self.machine_name,
            'roomId': self.room_id,
            'roomName': self.room_name,
            'qrCode': self.qr_code,
            'modeId': self.mode_id,
            'modeName': self.mode_name,
            'scheduleType': self.schedule_type,
            'targetTime': to_iso(self.target_time),
            'weekday': self.weekday,
            'timeOfDay': self.time_of_day,
            'timeZone': self.timezone_name,
            'leadMinutes': self.lead_minutes,
            'status': self.status,
            'activeOrderNo': self.active_order_no,
            'startAt': to_iso(self.start_at),
            'holdUntil': to_iso(self.hold_until),
            'lastCheckedAt': to_iso(self.last_checked_at),
            'lastError': self.last_error,
            'lastRunAt': to_iso(self.last_run_at),
            'createdAt': to_iso(self.created_at),
            'updatedAt': to_iso(self.updated_at),
            'lastEvent': last_event,
        }


class ReservationService:
    def __init__(self) -> None:
        database.init()
        self._workflow_manager: Any | None = None

    def _fetch_task(self, task_id: int) -> ReservationTask | None:
        row = database.fetch_one('SELECT * FROM reservation_tasks WHERE id = ?', (task_id,))
        return ReservationTask.from_row(row) if row else None

    def _fetch_last_event(self, task_id: int) -> Dict[str, Any] | None:
        row = database.fetch_one(
            '''
            SELECT event_type, message, payload, created_at
            FROM reservation_events
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (task_id,),
        )
        if not row:
            return None
        payload = None
        if row['payload']:
            try:
                payload = json.loads(row['payload'])
            except json.JSONDecodeError:
                payload = row['payload']
        return {
            'eventType': row['event_type'],
            'message': row['message'],
            'payload': payload,
            'createdAt': row['created_at'],
        }

    def _record_event(self, task_id: int, event_type: str, message: str, payload: Dict[str, Any] | None = None) -> None:
        database.execute(
            '''
            INSERT INTO reservation_events(task_id, event_type, message, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (task_id, event_type, message, json.dumps(payload, ensure_ascii=False) if payload is not None else None, to_iso(now_local())),
        )

    def _notify(self, title: str, content: str) -> None:
        settings = settings_store.get_effective_settings()
        pushplus_notifier.notify(settings.pushplus_url, title, content)

    def _build_task_windows(self, target_time: datetime, lead_minutes: int) -> tuple[str, str]:
        start_at, hold_until = build_windows(target_time, lead_minutes)
        return to_iso(start_at) or '', to_iso(hold_until) or ''

    def _get_workflow_manager(self):
        if self._workflow_manager is None:
            from services.workflow import WorkflowManager

            self._workflow_manager = WorkflowManager()
        return self._workflow_manager

    def _find_active_process_id(self, order_no: str | None) -> str | None:
        normalized = str(order_no or '').strip()
        if not normalized:
            return None
        row = database.fetch_one(
            '''
            SELECT process_id
            FROM workflow_processes
            WHERE order_no = ?
              AND completed = 0
              AND terminated = 0
            ORDER BY updated_at DESC
            LIMIT 1
            ''',
            (normalized,),
        )
        return str(row['process_id']) if row else None

    def _ensure_process_for_task(
        self,
        task: ReservationTask,
        token: str,
        order_no: str | None,
        detail: Dict[str, Any] | None = None,
    ) -> str | None:
        normalized = str(order_no or '').strip()
        if not normalized or not task.qr_code:
            return None

        try:
            self._get_workflow_manager().ensure_process_for_order(
                token=token,
                qr_code=task.qr_code,
                mode_id=task.mode_id,
                order_no=normalized,
                goods_id=task.machine_id if task.machine_source != 'scan' else None,
                detail=detail,
            )
        except Exception:  # noqa: BLE001
            return None
        return self._find_active_process_id(normalized)

    def _extract_order_finish_time(self, detail: Dict[str, Any]) -> Any:
        order_item = (detail.get('orderItemList') or [{}])[0]
        fulfill_info = detail.get('fulfillInfo') or {}
        fulfilling_item = fulfill_info.get('fulfillingItem') or {}
        return fulfilling_item.get('finishTime') or order_item.get('finishTime') or detail.get('finishTime')

    def _serialize_current_order(self, order: Dict[str, Any] | None) -> str | None:
        if not order:
            return None
        return json.dumps(order, ensure_ascii=False)

    def _deserialize_current_order(self, snapshot: str | None) -> Dict[str, Any] | None:
        if not snapshot:
            return None
        try:
            payload = json.loads(snapshot)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _normalize_current_order(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        order_item = (detail.get('orderItemList') or [{}])[0]
        buttons = detail.get('buttonSwitch') or {}
        return {
            'orderNo': detail.get('orderNo', ''),
            'state': detail.get('state'),
            'stateDesc': detail.get('stateDesc') or '未知状态',
            'pageCode': detail.get('pageCode') or '',
            'price': detail.get('realPrice') or detail.get('payAmount') or '0.00',
            'createTime': detail.get('createTime'),
            'payTime': detail.get('payTime'),
            'completeTime': detail.get('completeTime'),
            'finishTime': self._extract_order_finish_time(detail),
            'invalidTime': detail.get('invalidTime'),
            'machineName': order_item.get('goodsName') or detail.get('deviceName') or '未知设备',
            'modeName': order_item.get('goodsItemName') or '未知模式',
            'shopName': order_item.get('shopName') or (detail.get('positionInfo') or {}).get('positionName') or '',
            'machineGoodsId': order_item.get('goodsId'),
            'buttonSwitch': {
                'canCancel': bool(buttons.get('canCancel')),
                'canCloseOrder': bool(buttons.get('canCloseOrder')),
                'canPay': bool(buttons.get('canPay')),
            },
        }

    def _get_snapshot_invalid_at(self, snapshot: Dict[str, Any] | None) -> datetime | None:
        if not snapshot:
            return None
        return parse_remote_order_time(str(snapshot.get('invalidTime') or '').strip() or None)

    def _get_snapshot_early_renew_at(self, snapshot: Dict[str, Any] | None) -> datetime | None:
        invalid_at = self._get_snapshot_invalid_at(snapshot)
        if invalid_at is None:
            return None
        return invalid_at - timedelta(seconds=EARLY_RENEW_LEAD_SECONDS)

    def _is_early_renew_due(
        self,
        snapshot: Dict[str, Any] | None,
        reference: datetime | None = None,
    ) -> bool:
        invalid_at = self._get_snapshot_invalid_at(snapshot)
        early_renew_at = self._get_snapshot_early_renew_at(snapshot)
        if invalid_at is None or early_renew_at is None:
            return False
        ref = reference or now_local()
        return early_renew_at <= ref < invalid_at

    def _is_final_pending_stage(self, detail: Dict[str, Any]) -> bool:
        if self._classify_order_detail(detail) != 'pending':
            return False
        page_code = str(detail.get('pageCode') or '')
        can_pay = bool((detail.get('buttonSwitch') or {}).get('canPay'))
        return can_pay or page_code == 'waiting_choose_ump'

    def _settle_pending_order_detail(
        self,
        client: HaierClient,
        order_no: str,
        detail: Dict[str, Any],
        *,
        max_checks: int = 4,
    ) -> Dict[str, Any]:
        normalized_order_no = str(order_no or '').strip()
        current = detail if isinstance(detail, dict) else {}
        if not normalized_order_no:
            return current

        checks = 0
        while checks < max_checks and self._classify_order_detail(current) == 'pending' and not self._is_final_pending_stage(current):
            next_res = client.order_detail(normalized_order_no)
            if not next_res.get('ok'):
                return current
            next_detail = next_res.get('data') or {}
            if not isinstance(next_detail, dict) or not next_detail:
                return current
            current = next_detail
            checks += 1

        if self._is_final_pending_stage(current):
            final_res = client.order_detail(normalized_order_no)
            if final_res.get('ok'):
                final_detail = final_res.get('data') or {}
                if isinstance(final_detail, dict) and final_detail:
                    current = final_detail

        return current

    def _ensure_final_pending_order(
        self,
        client: HaierClient,
        order_no: str,
        detail: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None, Dict[str, Any], str | None]:
        normalized_order_no = str(order_no or '').strip()
        current = detail if isinstance(detail, dict) else {}
        debug: Dict[str, Any] = {'initialDetail': current}
        if not normalized_order_no:
            return False, None, debug, '缺少 orderNo，无法推进到待付款状态。'

        classification = self._classify_order_detail(current)
        if classification == 'manual_check_required':
            return False, current if isinstance(current, dict) else None, debug, '订单进入了门店详情下单的待验证阶段，当前扫码预约不能接管。'
        if classification != 'pending':
            return False, current if isinstance(current, dict) else None, debug, '订单当前不是待支付状态。'

        if not self._is_final_pending_stage(current):
            place_res = client.place_clothes(normalized_order_no)
            debug['placeClothes'] = place_res.get('raw')
            if not place_res.get('ok'):
                detail_res = client.order_detail(normalized_order_no)
                debug['orderDetail'] = detail_res.get('raw')
                if not detail_res.get('ok'):
                    return False, current if isinstance(current, dict) else None, debug, place_res.get('msg') or '自动放入衣服失败。'
                current = detail_res.get('data') or {}
            else:
                detail_res = client.order_detail(normalized_order_no)
                debug['orderDetail'] = detail_res.get('raw')
                if not detail_res.get('ok'):
                    return False, current if isinstance(current, dict) else None, debug, detail_res.get('msg') or '读取订单详情失败。'
                current = detail_res.get('data') or {}

        current = self._settle_pending_order_detail(client, normalized_order_no, current)
        debug['settledDetail'] = current
        classification = self._classify_order_detail(current)
        if classification == 'manual_check_required':
            return False, current if isinstance(current, dict) else None, debug, '订单进入了门店详情下单的待验证阶段，当前扫码预约不能接管。'
        if classification != 'pending' or not self._is_final_pending_stage(current):
            return False, current if isinstance(current, dict) else None, debug, '订单未能进入最终待付款状态。'
        return True, current, debug, None

    def _retry_order_detail(
        self,
        client: HaierClient,
        order_no: str,
        *,
        attempts: int = 3,
        delay_seconds: float = 0.35,
        until_closed: bool = False,
        until_not_pending: bool = False,
    ) -> Dict[str, Any] | None:
        normalized_order_no = str(order_no or '').strip()
        if not normalized_order_no:
            return None

        max_attempts = max(1, int(attempts))
        last_detail: Dict[str, Any] | None = None
        for attempt in range(max_attempts):
            detail_res = client.order_detail(normalized_order_no)
            if detail_res.get('ok'):
                detail = detail_res.get('data') or {}
                if isinstance(detail, dict) and detail:
                    last_detail = detail
                    classification = self._classify_order_detail(detail)
                    if until_closed and classification == 'closed':
                        return detail
                    if until_not_pending and classification != 'pending':
                        return detail
                    if not until_closed and not until_not_pending:
                        return detail
            if attempt < max_attempts - 1:
                time.sleep(delay_seconds)
        return last_detail

    def _sync_workflow_process(self, token: str, order_no: str) -> None:
        normalized_order_no = str(order_no or '').strip()
        if not token or not normalized_order_no:
            return
        try:
            self._get_workflow_manager().sync_process_for_order(token, normalized_order_no)
        except Exception:  # noqa: BLE001
            return

    def sync_task_order_snapshot(self, token: str, order_no: str) -> None:
        normalized_order_no = str(order_no or '').strip()
        if not token or not normalized_order_no:
            return

        client = HaierClient(token)
        detail_res = client.order_detail(normalized_order_no)
        if not detail_res.get('ok'):
            return

        detail = detail_res.get('data') or {}
        if self._classify_order_detail(detail) == 'pending':
            ok, ensured_detail, _, _ = self._ensure_final_pending_order(client, normalized_order_no, detail)
            if not ok or not ensured_detail:
                return
            detail = ensured_detail
        else:
            detail = self._settle_pending_order_detail(client, normalized_order_no, detail)
        snapshot = self._normalize_current_order(detail)
        classification = self._classify_order_detail(detail)
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE active_order_no = ?
              AND status IN ('scheduled', 'holding', 'paused')
            ''',
            (normalized_order_no,),
        )

        now = to_iso(now_local()) or ''
        for row in rows:
            task = ReservationTask.from_row(row)
            if classification == 'pending':
                if not self._find_active_process_id(normalized_order_no):
                    self._ensure_process_for_task(task, token, normalized_order_no, detail)
                self._update_task(
                    task.id,
                    status='holding',
                    current_order_snapshot=self._serialize_current_order(snapshot),
                    last_checked_at=now,
                    last_error=None,
                )
                continue

            if classification in {'completed', 'running'}:
                if task.schedule_type == 'weekly':
                    self._advance_weekly_task(task, '检测到订单已完成或已开始运行，任务已滚动到下一周。')
                else:
                    self._update_task(
                        task.id,
                        status='completed',
                        current_order_snapshot=self._serialize_current_order(snapshot),
                        last_checked_at=now,
                        last_run_at=now,
                        last_error=None,
                    )
                continue

            if classification == 'closed':
                self._update_task(
                    task.id,
                    status='scheduled',
                    active_order_no=None,
                    current_order_snapshot=None,
                    last_checked_at=now,
                    last_error='订单已失效，等待下一轮补建。',
                )
                continue

            self._update_task(
                task.id,
                current_order_snapshot=self._serialize_current_order(snapshot),
                last_checked_at=now,
                last_error='订单状态未知，继续保留当前预约任务。',
            )

    def list_tasks(self) -> list[Dict[str, Any]]:
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE status != 'deleted'
            ORDER BY target_time ASC, id DESC
            '''
        )
        tasks = [ReservationTask.from_row(row) for row in rows]
        items: list[Dict[str, Any]] = []
        token = settings_store.get_effective_settings().token
        client = HaierClient(token) if token else None
        for task in tasks:
            current_order = self._deserialize_current_order(task.current_order_snapshot)
            if client and task.active_order_no and current_order and self._classify_order_detail(current_order) == 'pending':
                if not self._is_final_pending_stage(current_order):
                    ok, ensured_detail, _, error_msg = self._ensure_final_pending_order(client, task.active_order_no, current_order)
                    if ok and ensured_detail:
                        settled_snapshot = self._normalize_current_order(ensured_detail)
                        task.current_order_snapshot = self._serialize_current_order(settled_snapshot)
                        current_order = settled_snapshot
                        self._update_task(
                            task.id,
                            current_order_snapshot=task.current_order_snapshot,
                            last_checked_at=to_iso(now_local()) or '',
                            last_error=None,
                        )
                    else:
                        current_order = None
                        self._update_task(
                            task.id,
                            last_checked_at=to_iso(now_local()) or '',
                            last_error=error_msg or '订单尚未进入最终待付款状态。',
                        )
                else:
                    settled_detail = self._settle_pending_order_detail(client, task.active_order_no, current_order)
                    settled_snapshot = self._normalize_current_order(settled_detail)
                    task.current_order_snapshot = self._serialize_current_order(settled_snapshot)
                    current_order = settled_snapshot
                    self._update_task(
                        task.id,
                        current_order_snapshot=task.current_order_snapshot,
                        last_checked_at=to_iso(now_local()) or '',
                        last_error=None,
                    )
            item = task.to_dict(self._fetch_last_event(task.id))
            item['processId'] = self._find_active_process_id(task.active_order_no)
            item['currentOrder'] = current_order
            items.append(item)
        return items

    def _has_conflict(self, machine_source: str, machine_id: str, schedule_type: str, target_time: datetime, lead_minutes: int, weekday: int | None, time_of_day: str | None, timezone_name: str | None = None) -> bool:
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE machine_source = ?
              AND machine_id = ?
              AND status IN ('scheduled', 'holding', 'paused')
            ''',
            (machine_source, machine_id),
        )
        start_at, hold_until = build_windows(target_time, lead_minutes)
        for row in rows:
            task = ReservationTask.from_row(row)
            other_start, other_end = build_windows(task.target_time, task.lead_minutes)
            if task.schedule_type == 'weekly' and schedule_type == 'weekly':
                if task.weekday == weekday and task.time_of_day == time_of_day and task.timezone_name == timezone_name:
                    return True
            if start_at < other_end and hold_until > other_start:
                return True
        return False

    def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get('title') or '').strip()
        machine_source = str(payload.get('machineSource') or '').strip() or 'scan'
        machine_id = str(payload.get('machineId') or '').strip()
        machine_name = str(payload.get('machineName') or '').strip()
        room_id = str(payload.get('roomId') or '').strip() or None
        room_name = str(payload.get('roomName') or '').strip() or None
        qr_code = str(payload.get('qrCode') or '').strip() or None
        schedule_type = str(payload.get('scheduleType') or '').strip() or 'once'
        mode_name = str(payload.get('modeName') or '').strip()
        try:
            mode_id = int(payload.get('modeId'))
        except (TypeError, ValueError):
            raise ValueError('预约任务缺少有效的模式编号')
        try:
            lead_minutes = int(payload.get('leadMinutes') or settings_store.get_effective_settings().default_lead_minutes or DEFAULT_LEAD_MINUTES)
        except (TypeError, ValueError):
            raise ValueError('提前建单分钟数必须是正整数')

        if lead_minutes <= 0:
            raise ValueError('提前建单分钟数必须大于 0')
        if not machine_id or not machine_name:
            raise ValueError('预约任务缺少机器信息')
        if not mode_name:
            raise ValueError('预约任务缺少模式名称')
        if not qr_code:
            raise ValueError('当前预约仅支持可虚拟扫码的机器，请重新选择支持虚拟扫码的设备')
        if schedule_type not in {'once', 'weekly'}:
            raise ValueError('预约类型仅支持 once 或 weekly')

        weekday = payload.get('weekday')
        time_of_day = str(payload.get('timeOfDay') or '').strip() or None
        timezone_name = None
        if schedule_type == 'once':
            target_time_raw = str(payload.get('targetTime') or '').strip()
            if not target_time_raw:
                raise ValueError('单次预约必须提供目标时间')
            target_time = parse_iso(target_time_raw)
            if target_time is None:
                raise ValueError('目标时间格式无效')
            if target_time <= now_local():
                raise ValueError('目标时间必须晚于当前时间')
        else:
            try:
                weekday_value = int(weekday)
            except (TypeError, ValueError):
                raise ValueError('每周预约必须提供星期')
            if not time_of_day:
                raise ValueError('每周预约必须提供时间')
            weekday = weekday_value
            timezone_name = normalize_timezone_name(payload.get('timeZone'))
            target_time = next_weekly_target(weekday_value, time_of_day, timezone_name=timezone_name)

        if self._has_conflict(
            machine_source,
            machine_id,
            schedule_type,
            target_time,
            lead_minutes,
            int(weekday) if weekday is not None else None,
            time_of_day,
            timezone_name,
        ):
            raise ValueError('同一台机器在相同时间窗口内已经存在活跃预约任务')

        if not title:
            title = f'{machine_name} · {mode_name}'

        start_at_iso, hold_until_iso = self._build_task_windows(target_time, lead_minutes)
        created_at = to_iso(now_local()) or ''
        task_id = database.execute(
            '''
            INSERT INTO reservation_tasks(
                title, machine_source, machine_id, machine_name, room_id, room_name, qr_code,
                mode_id, mode_name, schedule_type, target_time, weekday, time_of_day, timezone_name,
                lead_minutes, status, active_order_no, start_at, hold_until, last_checked_at,
                last_error, last_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', NULL, ?, ?, NULL, NULL, NULL, ?, ?)
            ''',
            (
                title,
                machine_source,
                machine_id,
                machine_name,
                room_id,
                room_name,
                qr_code,
                mode_id,
                mode_name,
                schedule_type,
                to_iso(target_time),
                weekday,
                time_of_day,
                timezone_name,
                lead_minutes,
                start_at_iso,
                hold_until_iso,
                created_at,
                created_at,
            ),
        )
        self._record_event(
            task_id,
            'task_created',
            '预约任务已创建。',
            {'targetTime': to_iso(target_time), 'leadMinutes': lead_minutes, 'timeZone': timezone_name},
        )
        task = self._fetch_task(task_id)
        return task.to_dict(self._fetch_last_event(task_id)) if task else {}

    def pause_task(self, task_id: int, reason: str = '用户手动暂停预约任务。') -> Dict[str, Any]:
        task = self._fetch_task(task_id)
        if not task or task.status == 'deleted':
            raise ValueError('预约任务不存在')
        updated_at = to_iso(now_local()) or ''
        database.execute(
            '''
            UPDATE reservation_tasks
            SET status = 'paused', last_error = ?, updated_at = ?
            WHERE id = ?
            ''',
            (reason, updated_at, task_id),
        )
        self._record_event(task_id, 'task_paused', reason)
        self._notify('预约任务已暂停', f'{task.title}\n{reason}')
        updated = self._fetch_task(task_id)
        return updated.to_dict(self._fetch_last_event(task_id)) if updated else {}

    def resume_task(self, task_id: int) -> Dict[str, Any]:
        task = self._fetch_task(task_id)
        if not task or task.status == 'deleted':
            raise ValueError('预约任务不存在')
        if task.schedule_type == 'once' and task.target_time + timedelta(minutes=10) <= now_local():
            raise ValueError('单次预约的保单窗口已经结束，请重新创建预约任务')
        updated_at = to_iso(now_local()) or ''
        target_time = None
        start_at = None
        hold_until = None
        if task.schedule_type == 'weekly':
            if task.weekday is None or not task.time_of_day:
                raise ValueError('周任务缺少周期配置，请重新创建任务')
            next_target = next_weekly_target(task.weekday, task.time_of_day, reference=now_local(), timezone_name=task.timezone_name)
            target_time = to_iso(next_target)
            start_at, hold_until = self._build_task_windows(next_target, task.lead_minutes)
        database.execute(
            '''
            UPDATE reservation_tasks
            SET status = 'scheduled',
                active_order_no = NULL,
                last_error = NULL,
                current_order_snapshot = NULL,
                updated_at = ?,
                target_time = COALESCE(?, target_time),
                start_at = COALESCE(?, start_at),
                hold_until = COALESCE(?, hold_until)
            WHERE id = ?
            ''',
            (updated_at, target_time, start_at, hold_until, task_id),
        )
        self._record_event(task_id, 'task_resumed', '预约任务已恢复。')
        updated = self._fetch_task(task_id)
        return updated.to_dict(self._fetch_last_event(task_id)) if updated else {}

    def delete_task(self, task_id: int) -> Dict[str, Any]:
        task = self._fetch_task(task_id)
        if not task or task.status == 'deleted':
            raise ValueError('预约任务不存在')
        updated_at = to_iso(now_local()) or ''
        database.execute(
            '''
            UPDATE reservation_tasks
            SET status = 'deleted', updated_at = ?
            WHERE id = ?
            ''',
            (updated_at, task_id),
        )
        self._record_event(task_id, 'task_deleted', '预约任务已删除。')
        return {'id': task_id, 'status': 'deleted'}

    def handle_manual_order_closed(self, order_no: str, action: str, detail: Dict[str, Any] | None = None) -> None:
        snapshot = self._serialize_current_order(self._normalize_current_order(detail or {})) if detail else None
        updated_at = to_iso(now_local()) or ''
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE active_order_no = ?
              AND status IN ('scheduled', 'holding')
            ''',
            (order_no,),
        )
        for row in rows:
            task = ReservationTask.from_row(row)
            reason = f'检测到用户手动{action}订单，已暂停自动重建。'
            database.execute(
                '''
                UPDATE reservation_tasks
                SET status = 'paused', last_error = ?, current_order_snapshot = ?, last_checked_at = ?, updated_at = ?
                WHERE id = ?
                ''',
                (reason, snapshot, updated_at, updated_at, task.id),
            )
            self._record_event(task.id, 'manual_order_closed', reason, {'orderNo': order_no})
            self._notify('预约任务已暂停', f'{task.title}\n{reason}')

    def _update_task(self, task_id: int, **changes: Any) -> None:
        if not changes:
            return
        fields = []
        params = []
        for key, value in changes.items():
            fields.append(f'{key} = ?')
            params.append(value)
        fields.append('updated_at = ?')
        params.append(to_iso(now_local()) or '')
        params.append(task_id)
        database.execute(f"UPDATE reservation_tasks SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def _collect_keyed_values(self, obj: Any, keys: set[str]) -> set[str]:
        found: set[str] = set()
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys and value not in (None, ''):
                    found.add(str(value).strip())
                found.update(self._collect_keyed_values(value, keys))
        elif isinstance(obj, list):
            for item in obj:
                found.update(self._collect_keyed_values(item, keys))
        return {item for item in found if item}

    def _build_machine_identifiers(self, qr_code: str, scan_data: Dict[str, Any]) -> set[str]:
        identifiers = {str(qr_code).strip()}
        identifiers.update(self._collect_keyed_values(scan_data, MACHINE_IDENTIFIER_KEYS))
        return {item for item in identifiers if item}

    def _order_matches_machine(self, order: Dict[str, Any], machine_identifiers: set[str]) -> bool:
        order_identifiers = self._collect_keyed_values(order, MACHINE_IDENTIFIER_KEYS)
        if machine_identifiers & order_identifiers:
            return True

        order_blob = json.dumps(order, ensure_ascii=False, sort_keys=True)
        return any(identifier and len(identifier) >= 6 and identifier in order_blob for identifier in machine_identifiers)

    def _candidate_sort_key(self, summary: Dict[str, Any], detail: Dict[str, Any] | None = None) -> str:
        detail_payload = detail if isinstance(detail, dict) else {}
        return str(
            summary.get('updateTime')
            or summary.get('gmtModified')
            or summary.get('createTime')
            or detail_payload.get('updateTime')
            or detail_payload.get('gmtModified')
            or detail_payload.get('createTime')
            or ''
        )

    def _build_lookup_issue(
        self,
        *,
        priority: int,
        message: str,
        order_no: str,
        source: str,
        debug: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return {
            'priority': priority,
            'message': message,
            'orderNo': order_no,
            'source': source,
            'debug': debug,
        }

    def _inspect_existing_pending_candidate(
        self,
        client: HaierClient,
        order_no: str,
        detail: Dict[str, Any],
        machine_identifiers: set[str],
    ) -> tuple[bool, Dict[str, Any] | None, str | None]:
        current = self._settle_pending_order_detail(client, order_no, detail if isinstance(detail, dict) else {})
        if not isinstance(current, dict) or not current:
            return False, None, '找到待支付候选订单，但详情为空。'
        if not self._order_matches_machine(current, machine_identifiers):
            return False, current, None

        classification = self._classify_order_detail(current)
        if classification == 'manual_check_required':
            return False, current, '找到同机待支付订单，但处于待验证阶段，暂不接手。'
        if classification != 'pending':
            return False, current, '找到候选订单，但详情已不是待支付状态。'
        if not self._is_final_pending_stage(current):
            page_code = str(current.get('pageCode') or '')
            if page_code == 'place_clothes':
                return False, current, '找到同机待支付订单，但仍处于 place_clothes，暂不接手。'
            return False, current, '找到同机待支付订单，但尚未进入最终待付款状态。'
        return True, current, None

    def _find_existing_pending_order(
        self,
        task: ReservationTask,
        client: HaierClient,
        qr_code: str,
        scan_data: Dict[str, Any],
        excluded_order_nos: set[str] | None = None,
    ) -> tuple[tuple[str, Dict[str, Any]] | None, str | None, Dict[str, Any] | None]:
        excluded = {str(item).strip() for item in (excluded_order_nos or set()) if str(item).strip()}
        machine_identifiers = self._build_machine_identifiers(qr_code, scan_data)
        candidates: list[tuple[str, str, Dict[str, Any]]] = []
        seen_order_nos: set[str] = set()
        best_issue: Dict[str, Any] | None = None

        def remember_issue(issue: Dict[str, Any] | None) -> None:
            nonlocal best_issue
            if not issue:
                return
            if best_issue is None or int(issue.get('priority') or 0) > int(best_issue.get('priority') or 0):
                best_issue = issue

        def inspect_orders(orders: Iterable[Dict[str, Any]], source: str) -> None:
            for order in orders or []:
                order_no = str((order or {}).get('orderNo') or '').strip()
                if not order_no or order_no in excluded or order_no in seen_order_nos:
                    continue
                seen_order_nos.add(order_no)

                summary = order if isinstance(order, dict) else {}
                summary_classification = self._classify_order_detail(summary)
                summary_matches_machine = self._order_matches_machine(summary, machine_identifiers)
                should_inspect = summary_classification == 'pending' or (summary_matches_machine and summary_classification == 'unknown')
                if not should_inspect:
                    continue

                debug: Dict[str, Any] = {'source': source, 'summary': summary}
                detail_res = client.order_detail(order_no)
                debug['orderDetail'] = detail_res.get('raw')
                if not detail_res.get('ok'):
                    remember_issue(
                        self._build_lookup_issue(
                            priority=20,
                            message='找到待支付候选订单，但详情读取失败。',
                            order_no=order_no,
                            source=source,
                            debug=debug,
                        )
                    )
                    continue

                detail = detail_res.get('data') or {}
                ok, settled_detail, reason = self._inspect_existing_pending_candidate(client, order_no, detail, machine_identifiers)
                debug['settledDetail'] = settled_detail
                if ok and settled_detail:
                    candidates.append((self._candidate_sort_key(summary, settled_detail), order_no, settled_detail))
                    continue

                if settled_detail and not self._order_matches_machine(settled_detail, machine_identifiers):
                    if summary_matches_machine:
                        remember_issue(
                            self._build_lookup_issue(
                                priority=10,
                                message='找到候选订单，但详情确认不是当前机器。',
                                order_no=order_no,
                                source=source,
                                debug=debug,
                            )
                        )
                    continue

                if reason:
                    priority = 25
                    if 'place_clothes' in reason:
                        priority = 40
                    elif '待验证' in reason:
                        priority = 35
                    remember_issue(
                        self._build_lookup_issue(
                            priority=priority,
                            message=reason,
                            order_no=order_no,
                            source=source,
                            debug=debug,
                        )
                    )

        orders_res = client.get_underway_orders()
        if orders_res.get('ok'):
            inspect_orders(orders_res.get('data') or [], 'underway')

        if not candidates:
            history_res = client.list_history_orders(page=1, page_size=HISTORY_ORDER_LOOKUP_PAGE_SIZE)
            if history_res.get('ok'):
                history_data = history_res.get('data') or {}
                inspect_orders(history_data.get('items') or [], 'history')

        if not candidates:
            if best_issue:
                return None, str(best_issue.get('message') or NO_ADOPTABLE_PENDING_ORDER_MESSAGE), best_issue.get('debug')
            return None, NO_ADOPTABLE_PENDING_ORDER_MESSAGE, None

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, order_no, detail = candidates[0]
        return (order_no, detail), None, None

    def _create_pending_order(
        self,
        task: ReservationTask,
        token: str,
        *,
        excluded_order_nos: set[str] | None = None,
    ) -> tuple[bool, str, Dict[str, Any] | None, str]:
        if not task.qr_code:
            return False, '缺少二维码编号，无法创建预约订单。', None, 'failed'
        client = HaierClient(token)
        scan_res = client.scan_goods(task.qr_code)
        if not scan_res.get('ok'):
            return False, scan_res.get('msg') or '扫描预约设备失败。', scan_res.get('raw'), 'failed'
        scan_data = scan_res.get('data') or {}
        goods_id = scan_data.get('goodsId')
        if not goods_id:
            return False, '扫码结果缺少 goodsId。', scan_res.get('raw'), 'failed'
        existing_order, _, _ = self._find_existing_pending_order(task, client, task.qr_code, scan_data, excluded_order_nos)
        if existing_order:
            order_no, detail = existing_order
            return True, order_no, detail, 'adopted'
        goods_detail_res = client.goods_details(str(goods_id))
        if not goods_detail_res.get('ok'):
            return False, goods_detail_res.get('msg') or '读取设备详情失败。', goods_detail_res.get('raw'), 'failed'
        create_res = client.create_scan_order(
            goods_id=str(goods_id),
            mode_id=task.mode_id,
            hash_key=str(scan_data.get('activityHashKey') or ''),
            goods_detail=goods_detail_res.get('data') or {},
        )
        if not create_res.get('ok'):
            if create_res.get('error_type') == 'business':
                existing_order, adoption_reason, adoption_debug = self._find_existing_pending_order(task, client, task.qr_code, scan_data, excluded_order_nos)
                if existing_order:
                    order_no, detail = existing_order
                    return True, order_no, detail, 'adopted'
                if adoption_reason and adoption_reason != NO_ADOPTABLE_PENDING_ORDER_MESSAGE:
                    return False, adoption_reason, adoption_debug or create_res.get('raw'), 'failed_business'
                return False, create_res.get('msg') or '创建预约订单失败。', create_res.get('raw'), 'failed_business'
            return False, create_res.get('msg') or '创建预约订单失败。', create_res.get('raw'), f"failed_{create_res.get('error_type') or 'unknown'}"
        order_data = create_res.get('data') or {}
        order_no = str(order_data.get('orderNo') or '').strip()
        if not order_no:
            return False, '创建预约订单成功，但未返回 orderNo。', create_res.get('raw'), 'failed'
        detail_res = client.order_detail(order_no)
        if not detail_res.get('ok'):
            return False, detail_res.get('msg') or '读取新建订单详情失败。', detail_res.get('raw'), 'failed'
        ok, detail, debug, error_msg = self._ensure_final_pending_order(client, order_no, detail_res.get('data') or {})
        if not ok or not detail:
            return False, error_msg or '订单未能进入最终待付款状态。', debug, 'failed'
        return True, order_no, detail, 'created'

    def _is_manual_check_stage(self, detail: Dict[str, Any]) -> bool:
        page_code = str(detail.get('pageCode') or '')
        state_desc = str(detail.get('stateDesc') or '')
        return page_code == 'waiting_check' or '待验证' in state_desc

    def _classify_order_detail(self, detail: Dict[str, Any]) -> str:
        state = int(detail.get('state') or 0)
        state_desc = str(detail.get('stateDesc') or '')
        page_code = str(detail.get('pageCode') or '')
        can_pay = bool((detail.get('buttonSwitch') or {}).get('canPay'))
        if state in COMPLETED_ORDER_STATES or '已完成' in state_desc:
            return 'completed'
        if state in RUNNING_ORDER_STATES or '进行中' in state_desc or '运行中' in state_desc or '洗衣中' in state_desc or '烘干中' in state_desc or '脱水中' in state_desc:
            return 'running'
        if state in CLOSED_ORDER_STATES or '关闭' in state_desc or '取消' in state_desc or '失效' in state_desc:
            return 'closed'
        if self._is_manual_check_stage(detail):
            return 'manual_check_required'
        if state in PENDING_ORDER_STATES or can_pay or '待支付' in state_desc or page_code in {'place_clothes', 'waiting_choose_ump'}:
            return 'pending'
        return 'unknown'

    def _advance_weekly_task(self, task: ReservationTask, message: str, keep_error: str | None = None) -> None:
        if task.weekday is None or not task.time_of_day:
            self._update_task(task.id, status='failed', last_error='周任务缺少周期配置', active_order_no=None)
            self._record_event(task.id, 'task_failed', '周任务缺少周期配置，已停止调度。')
            return
        reference = max(now_local(), task.target_time + timedelta(minutes=11))
        next_target = next_weekly_target(task.weekday, task.time_of_day, reference=reference, timezone_name=task.timezone_name)
        start_at_iso, hold_until_iso = self._build_task_windows(next_target, task.lead_minutes)
        self._update_task(
            task.id,
            status='scheduled',
            target_time=to_iso(next_target),
            start_at=start_at_iso,
            hold_until=hold_until_iso,
            active_order_no=None,
            current_order_snapshot=None,
            last_error=keep_error,
        )
        self._record_event(task.id, 'weekly_rolled', message, {'nextTargetTime': to_iso(next_target)})

    def next_poll_delay_seconds(self, max_interval: float) -> float:
        try:
            limit = float(max_interval)
        except (TypeError, ValueError):
            limit = 30.0
        limit = max(limit, 0.05)

        now = now_local()
        next_delay: float | None = None
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE status IN ('scheduled', 'holding')
            ORDER BY target_time ASC, id ASC
            '''
        )

        for row in rows:
            task = ReservationTask.from_row(row)
            start_at = task.start_at
            hold_until = task.hold_until
            if start_at is None or hold_until is None:
                start_at, hold_until = build_windows(task.target_time, task.lead_minutes)

            for deadline in (start_at, hold_until):
                if deadline and deadline > now:
                    delay = (deadline - now).total_seconds()
                    if next_delay is None or delay < next_delay:
                        next_delay = delay

            if task.status != 'holding':
                continue

            snapshot = self._deserialize_current_order(task.current_order_snapshot)
            if self._classify_order_detail(snapshot or {}) != 'pending':
                continue

            early_renew_at = self._get_snapshot_early_renew_at(snapshot)
            if early_renew_at and early_renew_at > now:
                delay = (early_renew_at - now).total_seconds()
                if next_delay is None or delay < next_delay:
                    next_delay = delay

        if next_delay is None:
            return limit
        return max(0.05, min(limit, next_delay))

    def process_due_tasks(self) -> Dict[str, int]:
        settings = settings_store.get_effective_settings()
        token = settings.token
        if not token:
            return {'processed': 0, 'created': 0, 'adopted': 0, 'recreated': 0, 'completed': 0}

        client = HaierClient(token)
        now = now_local()
        created_count = 0
        adopted_count = 0
        recreated_count = 0
        completed_count = 0
        rows = database.fetch_all(
            '''
            SELECT *
            FROM reservation_tasks
            WHERE status IN ('scheduled', 'holding')
            ORDER BY target_time ASC, id ASC
            '''
        )
        for row in rows:
            task = ReservationTask.from_row(row)
            start_at, hold_until = build_windows(task.target_time, task.lead_minutes)

            if now < start_at:
                continue

            if now > hold_until:
                if task.schedule_type == 'weekly':
                    self._advance_weekly_task(task, '上一个保单窗口结束，已滚动到下周。', keep_error='上一个保单窗口结束。')
                else:
                    self._update_task(
                        task.id,
                        status='failed',
                        active_order_no=None,
                        current_order_snapshot=None,
                        last_error='保单窗口已结束，任务未完成。',
                    )
                    self._record_event(task.id, 'task_failed', '保单窗口已结束，任务未完成。')
                    self._notify('预约任务未完成', f'{task.title}\n保单窗口已结束。')
                continue

            if not task.active_order_no:
                ok, result, detail, source = self._create_pending_order(task, token)
                if not ok:
                    self._update_task(task.id, last_error=str(result), last_checked_at=to_iso(now))
                    self._record_event(task.id, 'order_create_failed', str(result), {'detail': detail})
                    continue
                order_no = str(result)
                current_order_snapshot = self._serialize_current_order(
                    self._normalize_current_order(detail if isinstance(detail, dict) else {})
                )
                process_id = self._ensure_process_for_task(task, token, order_no, detail if isinstance(detail, dict) else None)
                if source == 'adopted':
                    adopted_count += 1
                    self._update_task(
                        task.id,
                        status='holding',
                        active_order_no=order_no,
                        current_order_snapshot=current_order_snapshot,
                        last_checked_at=to_iso(now),
                        last_run_at=to_iso(now),
                        last_error=None,
                    )
                    self._record_event(
                        task.id,
                        'existing_order_adopted',
                        '检测到当前机器已有最终待付款订单，已接管当前订单。',
                        {'orderNo': order_no, 'processId': process_id},
                    )
                    self._notify('预约已接管现有订单', f'{task.title}\n订单号：{order_no}')
                    continue
                created_count += 1
                self._update_task(
                    task.id,
                    status='holding',
                    active_order_no=order_no,
                    current_order_snapshot=current_order_snapshot,
                    last_checked_at=to_iso(now),
                    last_run_at=to_iso(now),
                    last_error=None,
                )
                self._record_event(task.id, 'order_created', '预约订单已创建，并已自动放入衣物进入待付款状态。', {'orderNo': order_no, 'processId': process_id})
                self._notify('预约订单已创建', f'{task.title}\n订单号：{order_no}')
                continue

            snapshot = self._deserialize_current_order(task.current_order_snapshot)
            classification = self._classify_order_detail(snapshot or {})
            expired_pending = False
            self._update_task(task.id, last_checked_at=to_iso(now), last_error=None)

            if classification == 'pending':
                if task.active_order_no and snapshot and not self._is_final_pending_stage(snapshot):
                    ok, ensured_detail, debug, error_msg = self._ensure_final_pending_order(client, task.active_order_no, snapshot)
                    if not ok or not ensured_detail:
                        self._update_task(
                            task.id,
                            active_order_no=None,
                            current_order_snapshot=None,
                            status='scheduled',
                            last_error=error_msg or '订单未能进入最终待付款状态。',
                            last_checked_at=to_iso(now),
                        )
                        self._record_event(
                            task.id,
                            'order_auto_place_failed',
                            error_msg or '订单未能进入最终待付款状态。',
                            {'orderNo': task.active_order_no, 'detail': debug},
                        )
                        continue
                    snapshot = self._normalize_current_order(ensured_detail)
                    classification = self._classify_order_detail(ensured_detail)
                    self._update_task(
                        task.id,
                        current_order_snapshot=self._serialize_current_order(snapshot),
                        last_checked_at=to_iso(now),
                        last_error=None,
                    )
                if task.status != 'holding':
                    self._update_task(task.id, status='holding')
                if self._is_early_renew_due(snapshot, now):
                    previous_order_no = str(task.active_order_no or '').strip()
                    refreshed_detail = self._retry_order_detail(client, previous_order_no)
                    if not refreshed_detail:
                        self._update_task(
                            task.id,
                            current_order_snapshot=self._serialize_current_order(snapshot) if snapshot else None,
                            last_checked_at=to_iso(now),
                            last_error='订单已到提前换单时间，但刷新旧订单状态失败。',
                        )
                        self._record_event(
                            task.id,
                            'order_early_refresh_failed',
                            '订单已到提前换单时间，但刷新旧订单状态失败。',
                            {'orderNo': previous_order_no},
                        )
                        continue
                    snapshot = self._normalize_current_order(refreshed_detail)
                    classification = self._classify_order_detail(refreshed_detail)
                    self._update_task(
                        task.id,
                        current_order_snapshot=self._serialize_current_order(snapshot),
                        last_checked_at=to_iso(now),
                        last_error=None,
                    )
                    if classification == 'pending' and self._is_early_renew_due(snapshot, now):
                        cancel_res = client.cancel_order(previous_order_no)
                        refreshed_old_detail = self._retry_order_detail(client, previous_order_no, until_closed=True)
                        if refreshed_old_detail:
                            snapshot = self._normalize_current_order(refreshed_old_detail)
                            classification = self._classify_order_detail(refreshed_old_detail)
                            self._update_task(
                                task.id,
                                current_order_snapshot=self._serialize_current_order(snapshot),
                                last_checked_at=to_iso(now),
                                last_error=None,
                            )
                            self._sync_workflow_process(token, previous_order_no)

                        if classification == 'closed':
                            ok, result, recreate_detail, source = self._create_pending_order(
                                task,
                                token,
                                excluded_order_nos={previous_order_no},
                            )
                            if not ok:
                                self._update_task(
                                    task.id,
                                    status='scheduled',
                                    active_order_no=None,
                                    current_order_snapshot=None,
                                    last_checked_at=to_iso(now),
                                    last_error=str(result),
                                )
                                self._record_event(
                                    task.id,
                                    'order_early_recreate_failed',
                                    '提前换单后新订单创建失败，等待下一轮重试。',
                                    {'orderNo': previous_order_no, 'detail': recreate_detail, 'reason': str(result)},
                                )
                                continue

                            order_no = str(result)
                            current_order_snapshot = self._serialize_current_order(
                                self._normalize_current_order(recreate_detail if isinstance(recreate_detail, dict) else {})
                            )
                            process_id = self._ensure_process_for_task(task, token, order_no, recreate_detail if isinstance(recreate_detail, dict) else None)
                            if source == 'adopted':
                                adopted_count += 1
                                self._update_task(
                                    task.id,
                                    status='holding',
                                    active_order_no=order_no,
                                    current_order_snapshot=current_order_snapshot,
                                    last_checked_at=to_iso(now),
                                    last_run_at=to_iso(now),
                                    last_error=None,
                                )
                                self._record_event(
                                    task.id,
                                    'existing_order_early_adopted',
                                    '提前换单后，接管了当前机器上的新最终待付款订单。',
                                    {'orderNo': order_no, 'previousOrderNo': previous_order_no, 'processId': process_id},
                                )
                                self._notify('预约已提前接管现有订单', f'{task.title}\n订单号：{order_no}')
                                continue

                            recreated_count += 1
                            self._update_task(
                                task.id,
                                status='holding',
                                active_order_no=order_no,
                                current_order_snapshot=current_order_snapshot,
                                last_checked_at=to_iso(now),
                                last_run_at=to_iso(now),
                                last_error=None,
                            )
                            self._record_event(
                                task.id,
                                'order_early_recreated',
                                '已在旧订单失效前提前换单，并进入待付款状态。',
                                {'orderNo': order_no, 'previousOrderNo': previous_order_no, 'processId': process_id},
                            )
                            self._notify('预约订单已提前换单', f'{task.title}\n新订单号：{order_no}')
                            continue

                        if classification == 'pending':
                            cancel_error = cancel_res.get('msg') or '提前换单失败，旧订单尚未关闭。'
                            self._update_task(
                                task.id,
                                current_order_snapshot=self._serialize_current_order(snapshot) if snapshot else None,
                                last_checked_at=to_iso(now),
                                last_error=cancel_error,
                            )
                            self._record_event(
                                task.id,
                                'order_early_cancel_failed',
                                '提前换单失败，旧订单尚未关闭。',
                                {
                                    'orderNo': previous_order_no,
                                    'reason': cancel_error,
                                    'cancelResult': cancel_res.get('raw'),
                                },
                            )
                            continue
                invalid_at = self._get_snapshot_invalid_at(snapshot)
                if invalid_at and now < invalid_at:
                    continue
                if invalid_at is None and now <= hold_until:
                    continue
                expired_pending = True
                refreshed_detail = self._retry_order_detail(client, task.active_order_no)
                if not refreshed_detail:
                    self._update_task(
                        task.id,
                        current_order_snapshot=self._serialize_current_order(snapshot) if snapshot else None,
                        last_checked_at=to_iso(now),
                        last_error='订单已到补单时间，但刷新旧订单状态失败。',
                    )
                    self._record_event(
                        task.id,
                        'order_refresh_failed',
                        '订单已到补单时间，但刷新旧订单状态失败。',
                        {'orderNo': task.active_order_no},
                    )
                    continue
                snapshot = self._normalize_current_order(refreshed_detail)
                classification = self._classify_order_detail(refreshed_detail)
                self._update_task(
                    task.id,
                    current_order_snapshot=self._serialize_current_order(snapshot),
                    last_checked_at=to_iso(now),
                    last_error=None,
                )
                if classification == 'pending':
                    invalid_at = self._get_snapshot_invalid_at(snapshot)
                    if invalid_at and now < invalid_at:
                        expired_pending = False
                        continue
                    if invalid_at is None and now <= hold_until:
                        expired_pending = False
                        continue

            if classification == 'pending' and expired_pending:
                previous_order_no = str(task.active_order_no or '').strip()
                ok, result, recreate_detail, source = self._create_pending_order(
                    task,
                    token,
                    excluded_order_nos={previous_order_no} if previous_order_no else None,
                )
                if not ok and previous_order_no and source == 'failed_business':
                    cancel_res = client.cancel_order(previous_order_no)
                    refreshed_old_detail = self._retry_order_detail(client, previous_order_no, until_closed=True)
                    if refreshed_old_detail:
                        snapshot = self._normalize_current_order(refreshed_old_detail)
                        classification = self._classify_order_detail(refreshed_old_detail)
                        self._update_task(
                            task.id,
                            current_order_snapshot=self._serialize_current_order(snapshot),
                            last_checked_at=to_iso(now),
                            last_error=None,
                        )
                        self._sync_workflow_process(token, previous_order_no)
                    if classification == 'closed':
                        ok, result, recreate_detail, source = self._create_pending_order(
                            task,
                            token,
                            excluded_order_nos={previous_order_no},
                        )

                if not ok:
                    self._update_task(
                        task.id,
                        current_order_snapshot=self._serialize_current_order(snapshot) if snapshot else None,
                        last_checked_at=to_iso(now),
                        last_error=str(result),
                    )
                    self._record_event(
                        task.id,
                        'order_recreate_blocked',
                        '旧订单未完全关闭，暂不补新单。',
                        {'orderNo': previous_order_no, 'detail': recreate_detail, 'reason': str(result)},
                    )
                    continue

                order_no = str(result)
                current_order_snapshot = self._serialize_current_order(
                    self._normalize_current_order(recreate_detail if isinstance(recreate_detail, dict) else {})
                )
                process_id = self._ensure_process_for_task(task, token, order_no, recreate_detail if isinstance(recreate_detail, dict) else None)
                if source == 'adopted':
                    adopted_count += 1
                    self._update_task(
                        task.id,
                        status='holding',
                        active_order_no=order_no,
                        current_order_snapshot=current_order_snapshot,
                        last_checked_at=to_iso(now),
                        last_run_at=to_iso(now),
                        last_error=None,
                    )
                    self._record_event(
                        task.id,
                        'existing_order_adopted',
                        '旧订单失效后，接管了当前机器上的新最终待付款订单。',
                        {'orderNo': order_no, 'previousOrderNo': previous_order_no, 'processId': process_id},
                    )
                    continue

                recreated_count += 1
                self._update_task(
                    task.id,
                    status='holding',
                    active_order_no=order_no,
                    current_order_snapshot=current_order_snapshot,
                    last_checked_at=to_iso(now),
                    last_run_at=to_iso(now),
                    last_error=None,
                )
                self._record_event(
                    task.id,
                    'order_recreated',
                    '旧订单关闭后，已自动补建新订单并进入待付款状态。',
                    {'orderNo': order_no, 'previousOrderNo': previous_order_no, 'processId': process_id},
                )
                continue

            if classification in {'completed', 'running'}:
                completed_count += 1
                state_desc = (snapshot or {}).get('stateDesc') or (snapshot or {}).get('state') or '未知状态'
                if task.schedule_type == 'weekly':
                    self._advance_weekly_task(task, '检测到订单已完成或已开始运行，任务已滚动到下一周。')
                else:
                    self._update_task(task.id, status='completed', last_run_at=to_iso(now), last_error=None)
                    self._record_event(task.id, 'task_completed', '检测到预约订单已完成或已开始运行，本次预约结束。', {'orderNo': task.active_order_no})
                self._notify('预约任务已完成', f'{task.title}\n订单状态：{state_desc}')
                continue

            if classification == 'closed':
                previous_order_no = task.active_order_no
                ok, result, recreate_detail, source = self._create_pending_order(task, token)
                if not ok:
                    self._update_task(
                        task.id,
                        active_order_no=None,
                        current_order_snapshot=None,
                        status='scheduled',
                        last_error=str(result),
                        last_checked_at=to_iso(now),
                    )
                    self._record_event(task.id, 'order_closed', '原订单已失效，等待下一轮补建。', {'orderNo': task.active_order_no})
                    continue
                order_no = str(result)
                current_order_snapshot = self._serialize_current_order(
                    self._normalize_current_order(recreate_detail if isinstance(recreate_detail, dict) else {})
                )
                process_id = self._ensure_process_for_task(task, token, order_no, recreate_detail if isinstance(recreate_detail, dict) else None)
                if source == 'adopted':
                    adopted_count += 1
                    self._update_task(
                        task.id,
                        status='holding',
                        active_order_no=order_no,
                        current_order_snapshot=current_order_snapshot,
                        last_checked_at=to_iso(now),
                        last_run_at=to_iso(now),
                        last_error=None,
                    )
                    self._record_event(
                        task.id,
                        'existing_order_adopted',
                        '原订单失效后，检测到当前机器已有新的最终待付款订单，已接管当前订单。',
                        {'orderNo': order_no, 'previousOrderNo': previous_order_no, 'processId': process_id},
                    )
                    self._notify('预约已接管现有订单', f'{task.title}\n订单号：{order_no}')
                    continue
                recreated_count += 1
                self._update_task(
                    task.id,
                    status='holding',
                    active_order_no=order_no,
                    current_order_snapshot=current_order_snapshot,
                    last_checked_at=to_iso(now),
                    last_run_at=to_iso(now),
                    last_error=None,
                )
                self._record_event(task.id, 'order_recreated', '检测到订单失效，已自动补建并进入待付款状态。', {'orderNo': order_no, 'detail': recreate_detail, 'processId': process_id})
                self._notify('预约订单已补建', f'{task.title}\n新订单号：{order_no}')
                continue

            self._update_task(task.id, last_error='订单状态未知，继续保留当前预约任务。', last_checked_at=to_iso(now))
            self._record_event(
                task.id,
                'order_unknown_state',
                '订单状态未知，继续保留当前预约任务。',
                {
                    'orderNo': task.active_order_no,
                    'state': (snapshot or {}).get('state'),
                    'stateDesc': (snapshot or {}).get('stateDesc'),
                },
            )

        return {
            'processed': len(rows),
            'created': created_count,
            'adopted': adopted_count,
            'recreated': recreated_count,
            'completed': completed_count,
        }


reservation_service = ReservationService()
