from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Literal

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


def now_local() -> datetime:
    return datetime.now().astimezone()


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def parse_time_of_day(value: str) -> tuple[int, int]:
    parts = value.split(':', 1)
    if len(parts) != 2:
        raise ValueError('时间格式必须为 HH:MM')
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError('时间格式必须为 HH:MM')
    return hour, minute


def next_weekly_target(weekday: int, time_of_day: str, reference: datetime | None = None) -> datetime:
    if weekday < 0 or weekday > 6:
        raise ValueError('每周预约的星期必须在 0-6 之间')
    hour, minute = parse_time_of_day(time_of_day)
    ref = reference or now_local()
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
    lead_minutes: int
    status: ReservationStatus
    active_order_no: str | None
    start_at: datetime | None
    hold_until: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
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
            lead_minutes=int(row['lead_minutes']),
            status=str(row['status']),
            active_order_no=str(row['active_order_no']) if row['active_order_no'] is not None else None,
            start_at=parse_iso(row['start_at']),
            hold_until=parse_iso(row['hold_until']),
            last_checked_at=parse_iso(row['last_checked_at']),
            last_error=str(row['last_error']) if row['last_error'] is not None else None,
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
        return [task.to_dict(self._fetch_last_event(task.id)) for task in tasks]

    def _has_conflict(self, machine_source: str, machine_id: str, schedule_type: str, target_time: datetime, lead_minutes: int, weekday: int | None, time_of_day: str | None) -> bool:
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
                if task.weekday == weekday and task.time_of_day == time_of_day:
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
            target_time = next_weekly_target(weekday_value, time_of_day)

        if self._has_conflict(machine_source, machine_id, schedule_type, target_time, lead_minutes, int(weekday) if weekday is not None else None, time_of_day):
            raise ValueError('同一台机器在相同时间窗口内已经存在活跃预约任务')

        if not title:
            title = f'{machine_name} · {mode_name}'

        start_at_iso, hold_until_iso = self._build_task_windows(target_time, lead_minutes)
        created_at = to_iso(now_local()) or ''
        task_id = database.execute(
            '''
            INSERT INTO reservation_tasks(
                title, machine_source, machine_id, machine_name, room_id, room_name, qr_code,
                mode_id, mode_name, schedule_type, target_time, weekday, time_of_day,
                lead_minutes, status, active_order_no, start_at, hold_until, last_checked_at,
                last_error, last_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', NULL, ?, ?, NULL, NULL, NULL, ?, ?)
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
                lead_minutes,
                start_at_iso,
                hold_until_iso,
                created_at,
                created_at,
            ),
        )
        self._record_event(task_id, 'task_created', '预约任务已创建。', {'targetTime': to_iso(target_time), 'leadMinutes': lead_minutes})
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
        database.execute(
            '''
            UPDATE reservation_tasks
            SET status = 'scheduled', active_order_no = NULL, last_error = NULL, updated_at = ?
            WHERE id = ?
            ''',
            (updated_at, task_id),
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

    def handle_manual_order_closed(self, order_no: str, action: str) -> None:
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
                SET status = 'paused', last_error = ?, updated_at = ?
                WHERE id = ?
                ''',
                (reason, to_iso(now_local()) or '', task.id),
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

    def _create_pending_order(self, task: ReservationTask, token: str) -> tuple[bool, str, Dict[str, Any] | None]:
        if not task.qr_code:
            return False, '缺少二维码编号，无法创建预约订单。', None
        client = HaierClient(token)
        scan_res = client.scan_goods(task.qr_code)
        if not scan_res.get('ok'):
            return False, scan_res.get('msg') or '扫描预约设备失败。', scan_res.get('raw')
        scan_data = scan_res.get('data') or {}
        goods_id = scan_data.get('goodsId')
        if not goods_id:
            return False, '扫码结果缺少 goodsId。', scan_res.get('raw')
        create_res = client.create_scan_order(goods_id=str(goods_id), mode_id=task.mode_id, hash_key=str(scan_data.get('activityHashKey') or ''))
        if not create_res.get('ok'):
            return False, create_res.get('msg') or '创建预约订单失败。', create_res.get('raw')
        order_data = create_res.get('data') or {}
        order_no = str(order_data.get('orderNo') or '').strip()
        if not order_no:
            return False, '创建预约订单成功，但未返回 orderNo。', create_res.get('raw')
        detail_res = client.order_detail(order_no)
        if not detail_res.get('ok'):
            return False, detail_res.get('msg') or '读取新建订单详情失败。', detail_res.get('raw')
        return True, order_no, detail_res.get('data') or {}

    def _classify_order_detail(self, detail: Dict[str, Any]) -> str:
        state = int(detail.get('state') or 0)
        state_desc = str(detail.get('stateDesc') or '')
        page_code = str(detail.get('pageCode') or '')
        can_pay = bool((detail.get('buttonSwitch') or {}).get('canPay'))
        if state in COMPLETED_ORDER_STATES or '已完成' in state_desc:
            return 'completed'
        if state in RUNNING_ORDER_STATES or '进行中' in state_desc or '洗衣中' in state_desc:
            return 'running'
        if state in CLOSED_ORDER_STATES or '关闭' in state_desc or '取消' in state_desc:
            return 'closed'
        if state in PENDING_ORDER_STATES or can_pay or '待支付' in state_desc or '待验证' in state_desc or page_code in {'waiting_check', 'place_clothes', 'waiting_choose_ump'}:
            return 'pending'
        return 'unknown'

    def _advance_weekly_task(self, task: ReservationTask, message: str, keep_error: str | None = None) -> None:
        if task.weekday is None or not task.time_of_day:
            self._update_task(task.id, status='failed', last_error='周任务缺少周期配置', active_order_no=None)
            self._record_event(task.id, 'task_failed', '周任务缺少周期配置，已停止调度。')
            return
        next_target = next_weekly_target(task.weekday, task.time_of_day, reference=task.target_time + timedelta(minutes=11))
        start_at_iso, hold_until_iso = self._build_task_windows(next_target, task.lead_minutes)
        self._update_task(
            task.id,
            status='scheduled',
            target_time=to_iso(next_target),
            start_at=start_at_iso,
            hold_until=hold_until_iso,
            active_order_no=None,
            last_error=keep_error,
        )
        self._record_event(task.id, 'weekly_rolled', message, {'nextTargetTime': to_iso(next_target)})

    def process_due_tasks(self) -> Dict[str, int]:
        settings = settings_store.get_effective_settings()
        token = settings.token
        if not token:
            return {'processed': 0, 'created': 0, 'recreated': 0, 'completed': 0}

        now = now_local()
        created_count = 0
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
                    self._update_task(task.id, status='failed', active_order_no=None, last_error='保单窗口已结束，任务未完成。')
                    self._record_event(task.id, 'task_failed', '保单窗口已结束，任务未完成。')
                    self._notify('预约任务未完成', f'{task.title}\n保单窗口已结束。')
                continue

            if not task.active_order_no:
                ok, result, detail = self._create_pending_order(task, token)
                if not ok:
                    self._update_task(task.id, last_error=str(result), last_checked_at=to_iso(now))
                    self._record_event(task.id, 'order_create_failed', str(result), {'detail': detail})
                    continue
                created_count += 1
                order_no = str(result)
                self._update_task(task.id, status='holding', active_order_no=order_no, last_checked_at=to_iso(now), last_run_at=to_iso(now), last_error=None)
                self._record_event(task.id, 'order_created', '预约订单已创建并进入保单窗口。', {'orderNo': order_no})
                self._notify('预约订单已创建', f'{task.title}\n订单号：{order_no}')
                continue

            client = HaierClient(token)
            detail_res = client.order_detail(task.active_order_no)
            if not detail_res.get('ok'):
                self._update_task(task.id, last_error=detail_res.get('msg') or '读取订单详情失败。', last_checked_at=to_iso(now))
                self._record_event(task.id, 'order_detail_failed', detail_res.get('msg') or '读取订单详情失败。')
                continue

            detail = detail_res.get('data') or {}
            classification = self._classify_order_detail(detail)
            self._update_task(task.id, last_checked_at=to_iso(now), last_error=None)

            if classification == 'pending':
                if task.status != 'holding':
                    self._update_task(task.id, status='holding')
                continue

            if classification in {'completed', 'running'}:
                completed_count += 1
                if task.schedule_type == 'weekly':
                    self._advance_weekly_task(task, '检测到订单已支付并开始运行，任务已滚动到下一周。')
                else:
                    self._update_task(task.id, status='completed', last_run_at=to_iso(now))
                    self._record_event(task.id, 'task_completed', '检测到预约订单已支付完成，本次预约结束。', {'orderNo': task.active_order_no})
                self._notify('预约任务已完成', f'{task.title}\n订单状态：{detail.get("stateDesc") or detail.get("state")}')
                continue

            if classification == 'closed':
                ok, result, recreate_detail = self._create_pending_order(task, token)
                if not ok:
                    self._update_task(task.id, active_order_no=None, status='scheduled', last_error=str(result), last_checked_at=to_iso(now))
                    self._record_event(task.id, 'order_closed', '原订单已失效，等待下一轮补建。', {'orderNo': task.active_order_no})
                    continue
                recreated_count += 1
                order_no = str(result)
                self._update_task(task.id, status='holding', active_order_no=order_no, last_checked_at=to_iso(now), last_run_at=to_iso(now), last_error=None)
                self._record_event(task.id, 'order_recreated', '检测到订单失效，已自动补建。', {'orderNo': order_no, 'detail': recreate_detail})
                self._notify('预约订单已补建', f'{task.title}\n新订单号：{order_no}')
                continue

            self._update_task(task.id, last_error='订单状态未知，继续保留当前预约任务。', last_checked_at=to_iso(now))
            self._record_event(task.id, 'order_unknown_state', '订单状态未知，继续保留当前预约任务。', {'orderNo': task.active_order_no, 'state': detail.get('state'), 'stateDesc': detail.get('stateDesc')})

        return {
            'processed': len(rows),
            'created': created_count,
            'recreated': recreated_count,
            'completed': completed_count,
        }


reservation_service = ReservationService()
