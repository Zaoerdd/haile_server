from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from services.db import database
from services.haier_client import HaierClient


STEP_LABELS = {
    1: '解析机器',
    2: '创建订单',
    3: '确认放衣',
    4: '生成预支付',
    5: '支付启动',
    6: '已完成',
}

MANUAL_SCAN_FLOW_TYPE = 'scan_manual'
ATTACHED_SCAN_FLOW_TYPE = 'scan_attached'

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

AUTO_CLEANUP_ALLOW_KEYWORDS = (
    '待',
    '未支付',
    '待支付',
    '待确认',
    '待放衣',
    '待启动',
    '支付中',
    '关门',
    '开门',
    '创建',
    '锁门',
)

AUTO_CLEANUP_BLOCK_KEYWORDS = (
    '洗涤中',
    '运行中',
    '脱水中',
    '烘干中',
    '已启动',
    '预约中',
    '已完成',
    '完成',
)

PENDING_ORDER_STATES = {50}
CLOSED_ORDER_STATES = {401, 411}
RUNNING_ORDER_STATES = {500}
COMPLETED_ORDER_STATES = {1000}
ACTIVE_PAGE_CODES = {'place_clothes', 'waiting_choose_ump'}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class ProcessState:
    process_id: str
    qr_code: str
    mode_id: int
    flow_type: str = 'scan'
    current_step: int = 1
    completed: bool = False
    terminated: bool = False
    blocked_reason: str | None = None
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_row(cls, row: Any) -> 'ProcessState':
        return cls(
            process_id=str(row['process_id']),
            qr_code=str(row['qr_code']),
            mode_id=int(row['mode_id']),
            flow_type=str(row['flow_type'] or 'scan'),
            current_step=int(row['current_step'] or 1),
            completed=bool(row['completed']),
            terminated=bool(row['terminated']),
            blocked_reason=str(row['blocked_reason']) if row['blocked_reason'] is not None else None,
            context={
                'goods_id': row['goods_id'],
                'hash_key': row['hash_key'],
                'order_no': row['order_no'],
                'prepay_param': row['prepay_param'],
            },
            created_at=str(row['created_at']),
            updated_at=str(row['updated_at']),
        )

    def to_record(self) -> tuple[Any, ...]:
        return (
            self.process_id,
            self.flow_type,
            self.qr_code,
            self.mode_id,
            self.current_step,
            1 if self.completed else 0,
            1 if self.terminated else 0,
            self.blocked_reason,
            self.context.get('goods_id'),
            self.context.get('hash_key'),
            self.context.get('order_no'),
            self.context.get('prepay_param'),
            self.created_at,
            self.updated_at,
        )

    def to_dict(self) -> Dict[str, Any]:
        context_summary = {
            'goodsId': self.context.get('goods_id'),
            'orderNo': self.context.get('order_no'),
            'prepayReady': bool(self.context.get('prepay_param')),
        }
        can_continue = bool(self.context.get('order_no')) and not self.completed and not self.terminated
        if self.current_step == 1 and not self.context.get('order_no'):
            can_continue = not self.completed and not self.terminated
        return {
            'processId': self.process_id,
            'flowType': self.flow_type,
            'qrCode': self.qr_code,
            'modeId': self.mode_id,
            'currentStep': self.current_step,
            'currentStepLabel': self.step_label,
            'completed': self.completed,
            'terminated': self.terminated,
            'blockedReason': self.blocked_reason,
            'canContinue': can_continue,
            'contextSummary': context_summary,
            'createdAt': self.created_at,
            'updatedAt': self.updated_at,
        }

    @property
    def step_label(self) -> str:
        if self.completed:
            return STEP_LABELS[6]
        if self.terminated:
            return '已终止'
        return STEP_LABELS.get(self.current_step, '未知步骤')


class WorkflowManager:
    def __init__(self) -> None:
        database.init()

    def start_process(self, token: str, qr_code: str, mode_id: int) -> Dict[str, Any]:
        cleanup_result = self.cleanup_machine_orders(token=token, qr_code=qr_code)
        if cleanup_result.get('status') != 'success':
            return cleanup_result

        state = ProcessState(
            process_id=uuid.uuid4().hex,
            qr_code=qr_code,
            mode_id=int(mode_id),
            flow_type=MANUAL_SCAN_FLOW_TYPE,
        )
        self._save_state(state)
        return {
            'status': 'success',
            'msg': self._build_start_message(cleanup_result),
            'process': state.to_dict(),
            'cleanup': cleanup_result,
            'debug': cleanup_result.get('debug'),
        }

    def run_full_process(self, token: str, qr_code: str, mode_id: int) -> Dict[str, Any]:
        start_result = self.start_process(token=token, qr_code=qr_code, mode_id=mode_id)
        if start_result.get('status') != 'success':
            return start_result

        process = start_result.get('process') or {}
        process_id = process.get('processId')
        if not process_id:
            return self._error('process_not_found', '流程已创建，但未返回 processId。')

        last_result = start_result
        while True:
            current = last_result.get('process') or {}
            if current.get('completed'):
                return last_result
            next_result = self.execute_next(process_id=process_id, token=token)
            if next_result.get('status') != 'success':
                next_result['cleanup'] = start_result.get('cleanup')
                return next_result
            last_result = next_result

    def reset_process(self, process_id: str, token: Optional[str] = None, cleanup_remote: bool = False) -> Dict[str, Any]:
        state = self.get(process_id)
        if not state:
            return {'status': 'success', 'msg': '流程已重置。'}

        cleanup_result: Optional[Dict[str, Any]] = None
        order_no = str(state.context.get('order_no') or '').strip()
        if cleanup_remote and token and not state.completed and order_no:
            cleanup_result = self.cleanup_order_by_no(token=token, order_no=order_no)
            if cleanup_result.get('status') != 'success':
                return {
                    'status': 'error',
                    'errorType': 'remote_cleanup_failed',
                    'msg': '云端订单清理失败，请稍后重试或在订单页手动结束。',
                    'cleanup': cleanup_result,
                    'process': state.to_dict(),
                    'debug': cleanup_result.get('debug'),
                }

        database.execute('DELETE FROM workflow_processes WHERE process_id = ?', (process_id,))

        message = '流程已重置。'
        if cleanup_result and cleanup_result.get('cleanedOrders'):
            message = f"流程已重置，并自动结束了 {len(cleanup_result['cleanedOrders'])} 笔云端订单。"

        payload: Dict[str, Any] = {'status': 'success', 'msg': message}
        if cleanup_result:
            payload['cleanup'] = cleanup_result
            payload['debug'] = cleanup_result.get('debug')
        return payload

    def get(self, process_id: str) -> Optional[ProcessState]:
        row = database.fetch_one('SELECT * FROM workflow_processes WHERE process_id = ?', (process_id,))
        return ProcessState.from_row(row) if row else None

    def get_by_order_no(self, order_no: str) -> Optional[ProcessState]:
        row = database.fetch_one(
            '''
            SELECT *
            FROM workflow_processes
            WHERE order_no = ?
            ORDER BY updated_at DESC
            LIMIT 1
            ''',
            (order_no,),
        )
        return ProcessState.from_row(row) if row else None

    def get_process_details(self, process_id: str, token: str) -> Optional[Dict[str, Any]]:
        state = self.get(process_id)
        if not state:
            return None
        return self._build_process_payload(state, token=token, sync_remote=True)

    def ensure_process_for_order(
        self,
        token: str,
        qr_code: str,
        mode_id: int,
        order_no: str,
        *,
        goods_id: str | None = None,
        hash_key: str | None = None,
        detail: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_order_no = str(order_no or '').strip()
        normalized_qr_code = str(qr_code or '').strip()
        if not normalized_order_no:
            raise ValueError('order_no is required')
        if not normalized_qr_code:
            raise ValueError('qr_code is required')

        state = self.get_by_order_no(normalized_order_no)
        if not state:
            state = ProcessState(
                process_id=uuid.uuid4().hex,
                qr_code=normalized_qr_code,
                mode_id=int(mode_id),
                flow_type=ATTACHED_SCAN_FLOW_TYPE,
                current_step=3,
            )
        else:
            state.qr_code = normalized_qr_code
            state.mode_id = int(mode_id)

        if not state.flow_type or state.flow_type == 'scan':
            state.flow_type = ATTACHED_SCAN_FLOW_TYPE
        state.context['order_no'] = normalized_order_no
        if goods_id:
            state.context['goods_id'] = str(goods_id)
        if hash_key is not None:
            state.context['hash_key'] = str(hash_key)
        elif 'hash_key' not in state.context:
            state.context['hash_key'] = ''

        client = HaierClient(token)
        if not state.context.get('goods_id') or not state.context.get('hash_key'):
            scan_res = client.scan_goods(normalized_qr_code)
            if scan_res.get('ok'):
                scan_data = scan_res.get('data') or {}
                scanned_goods_id = scan_data.get('goodsId')
                if scanned_goods_id and not state.context.get('goods_id'):
                    state.context['goods_id'] = str(scanned_goods_id)
                if not state.context.get('hash_key'):
                    state.context['hash_key'] = str(scan_data.get('activityHashKey') or '')

        if detail is None:
            detail_res = client.order_detail(normalized_order_no)
            if detail_res.get('ok'):
                detail = detail_res.get('data') or {}

        self._hydrate_context_from_detail(state, detail or {})
        classification = self._classify_order_detail(detail or {})
        if classification == 'closed':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '订单已关闭或失效，流程无法继续。'
        elif classification == 'manual_check_required':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '当前订单进入门店详情下单的待验证阶段，不能继续按扫码下单流程推进。'
        elif classification in {'running', 'completed'}:
            state.completed = True
            state.terminated = False
            state.current_step = 6
            state.blocked_reason = None
        elif classification == 'pending':
            self._apply_pending_detail_to_state(state, detail or {})
        else:
            state.completed = False
            state.terminated = False
            state.blocked_reason = None
            state.current_step = state.current_step if 1 <= state.current_step <= 5 else 3

        self._save_state(state)
        return state.to_dict()

    def list_active_processes(self, token: str) -> List[Dict[str, Any]]:
        rows = database.fetch_all(
            '''
            SELECT *
            FROM workflow_processes
            WHERE order_no IS NOT NULL
              AND completed = 0
              AND terminated = 0
            ORDER BY updated_at DESC
            '''
        )
        items: List[Dict[str, Any]] = []
        for row in rows:
            state = ProcessState.from_row(row)
            payload = self._build_process_payload(state, token=token, sync_remote=True)
            if payload.get('canContinue'):
                items.append(payload)
        return items

    def sync_process_for_order(self, token: str, order_no: str) -> Optional[Dict[str, Any]]:
        normalized_order_no = str(order_no or '').strip()
        if not normalized_order_no:
            return None

        state = self.get_by_order_no(normalized_order_no)
        if not state:
            return None

        client = HaierClient(token)
        result = self._sync_remote_order_state(state, client, fail_on_unavailable=False)
        self._save_state(state)
        return result

    def execute_next(self, process_id: str, token: str) -> Dict[str, Any]:
        state = self.get(process_id)
        if not state:
            return self._error('process_not_found', '流程不存在，请重新开始。')
        if state.terminated:
            return self._error('process_blocked', state.blocked_reason or '流程已终止，无法继续。', state)
        if state.completed:
            return self._error('already_completed', '流程已经完成，无需继续。', state)

        client = HaierClient(token)
        order_state_result = self._sync_remote_order_state(state, client)
        if order_state_result:
            self._save_state(state)
            return order_state_result

        validation_error = self._validate_preconditions(state)
        if validation_error:
            return validation_error

        handlers = {
            1: self._step_scan,
            2: self._step_create_order,
            3: self._step_place_clothes,
            4: self._step_prepare_payment,
            5: self._step_pay,
        }
        handler = handlers.get(state.current_step)
        if not handler:
            return self._error('invalid_step', f'未知步骤：{state.current_step}', state)

        result = handler(state, client)
        state.updated_at = now_iso()
        self._save_state(state)
        return result

    def cleanup_machine_orders(self, token: str, qr_code: str) -> Dict[str, Any]:
        client = HaierClient(token)
        scan_res = client.scan_goods(qr_code)
        if not scan_res.get('ok'):
            return self._error(
                'machine_scan_failed',
                '开始流程前扫描机器失败，无法确认是否存在遗留订单。',
                debug=scan_res.get('raw'),
                code=scan_res.get('code'),
                data=scan_res.get('data'),
            )

        scan_data = scan_res.get('data') or {}
        identifiers = self._build_machine_identifiers(qr_code, scan_data)

        orders_res = client.get_orders()
        if not orders_res.get('ok'):
            return self._error(
                'order_list_failed',
                '开始流程前读取进行中订单失败，无法确认是否存在遗留订单。',
                debug=orders_res.get('raw'),
                code=orders_res.get('code'),
                data=orders_res.get('data'),
            )

        matched_orders: List[Dict[str, Any]] = []
        cleaned_orders: List[Dict[str, Any]] = []
        blocked_orders: List[Dict[str, Any]] = []
        failed_orders: List[Dict[str, Any]] = []

        for order in orders_res.get('data') or []:
            if not self._order_matches_machine(order, identifiers):
                continue
            matched_orders.append(self._compact_order(order))
            if not self._is_safe_to_auto_finish(order):
                blocked_orders.append(self._compact_order(order))
                continue

            order_no = str(order.get('orderNo') or '').strip()
            if not order_no:
                failed_orders.append(
                    {
                        **self._compact_order(order),
                        'reason': '订单缺少 orderNo，无法自动结束。',
                    }
                )
                continue

            finish_res = client.finish_order(order_no)
            if finish_res.get('ok'):
                cleaned_orders.append(self._compact_order(order))
            else:
                failed_orders.append(
                    {
                        **self._compact_order(order),
                        'reason': finish_res.get('msg') or '自动结束失败。',
                        'debug': finish_res.get('raw'),
                    }
                )

        debug = {
            'scan': scan_res.get('raw'),
            'orderList': orders_res.get('raw'),
            'machineIdentifiers': sorted(identifiers),
        }
        if failed_orders or blocked_orders:
            message_parts: List[str] = []
            if blocked_orders:
                message_parts.append(f'发现 {len(blocked_orders)} 笔同机订单疑似已启动，未自动结束')
            if failed_orders:
                message_parts.append(f'另有 {len(failed_orders)} 笔同机订单自动结束失败')
            return {
                'status': 'error',
                'errorType': 'stale_order_cleanup_failed',
                'msg': '；'.join(message_parts) + '，请先检查订单后再重试。',
                'matchedOrders': matched_orders,
                'cleanedOrders': cleaned_orders,
                'blockedOrders': blocked_orders,
                'failedOrders': failed_orders,
                'debug': debug,
            }

        message = '未发现该机器的遗留订单。'
        if cleaned_orders:
            message = f"已自动结束 {len(cleaned_orders)} 笔该机器的遗留订单。"
        return {
            'status': 'success',
            'msg': message,
            'matchedOrders': matched_orders,
            'cleanedOrders': cleaned_orders,
            'blockedOrders': blocked_orders,
            'failedOrders': failed_orders,
            'debug': debug,
        }

    def cleanup_order_by_no(self, token: str, order_no: str) -> Dict[str, Any]:
        client = HaierClient(token)
        detail_res = client.order_detail(order_no)
        detail = detail_res.get('data') or {}
        classification = self._classify_order_detail(detail) if detail_res.get('ok') else 'unknown'

        if classification == 'closed':
            return {
                'status': 'success',
                'msg': '当前流程对应的云端订单已关闭，无需重复清理。',
                'matchedOrders': [{'orderNo': order_no}],
                'cleanedOrders': [{'orderNo': order_no}],
                'blockedOrders': [],
                'failedOrders': [],
                'debug': {'orderDetail': detail_res.get('raw')},
            }

        if classification == 'pending':
            cleanup_action = 'cancel'
            cleanup_res = client.cancel_order(order_no)
            success_msg = '已自动取消当前流程对应的云端订单。'
            failure_msg = '自动取消订单失败。'
            failure_reason = '自动取消失败。'
        else:
            cleanup_action = 'finish'
            cleanup_res = client.finish_order(order_no)
            success_msg = '已自动结束当前流程对应的云端订单。'
            failure_msg = '自动结束订单失败。'
            failure_reason = '自动结束失败。'

        if not cleanup_res.get('ok'):
            return {
                'status': 'error',
                'errorType': cleanup_res.get('error_type', 'remote_cleanup_failed'),
                'msg': cleanup_res.get('msg') or failure_msg,
                'matchedOrders': [{'orderNo': order_no}],
                'cleanedOrders': [],
                'blockedOrders': [],
                'failedOrders': [{'orderNo': order_no, 'reason': cleanup_res.get('msg') or failure_reason}],
                'debug': {
                    'action': cleanup_action,
                    'orderDetail': detail_res.get('raw'),
                    'cleanup': cleanup_res.get('raw'),
                },
            }
        return {
            'status': 'success',
            'msg': success_msg,
            'matchedOrders': [{'orderNo': order_no}],
            'cleanedOrders': [{'orderNo': order_no}],
            'blockedOrders': [],
            'failedOrders': [],
            'debug': {
                'action': cleanup_action,
                'orderDetail': detail_res.get('raw'),
                'cleanup': cleanup_res.get('raw'),
            },
        }

    def _build_process_payload(self, state: ProcessState, token: str, sync_remote: bool = False) -> Dict[str, Any]:
        client = HaierClient(token)
        order_summary = None
        can_continue = not state.completed and not state.terminated
        blocked_reason = state.blocked_reason

        if sync_remote and state.context.get('order_no'):
            try:
                sync_result = self._sync_remote_order_state(state, client, fail_on_unavailable=False)
                if sync_result and sync_result.get('status') == 'error' and sync_result.get('errorType') == 'order_sync_failed':
                    blocked_reason = sync_result.get('msg')
                    can_continue = False
                self._save_state(state)
            except Exception:  # noqa: BLE001
                blocked_reason = blocked_reason or '暂时无法同步订单状态。'
                can_continue = False

        order_no = str(state.context.get('order_no') or '').strip()
        if order_no:
            detail_res = client.order_detail(order_no)
            if detail_res.get('ok'):
                order_summary = self._normalize_order_summary(detail_res.get('data') or {})
            elif not blocked_reason:
                blocked_reason = detail_res.get('msg') or '暂时无法读取订单详情。'
                can_continue = False

        if state.completed or state.terminated:
            can_continue = False

        payload = state.to_dict()
        payload.update(
            {
                'orderNo': order_no or None,
                'order': order_summary,
                'canContinue': can_continue,
                'blockedReason': blocked_reason,
            }
        )
        return payload

    def _save_state(self, state: ProcessState) -> None:
        state.updated_at = now_iso()
        database.execute(
            '''
            INSERT INTO workflow_processes(
                process_id, flow_type, qr_code, mode_id, current_step, completed, terminated,
                blocked_reason, goods_id, hash_key, order_no, prepay_param, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(process_id) DO UPDATE SET
                flow_type = excluded.flow_type,
                qr_code = excluded.qr_code,
                mode_id = excluded.mode_id,
                current_step = excluded.current_step,
                completed = excluded.completed,
                terminated = excluded.terminated,
                blocked_reason = excluded.blocked_reason,
                goods_id = excluded.goods_id,
                hash_key = excluded.hash_key,
                order_no = excluded.order_no,
                prepay_param = excluded.prepay_param,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            ''',
            state.to_record(),
        )

    def _validate_preconditions(self, state: ProcessState) -> Optional[Dict[str, Any]]:
        step = state.current_step
        ctx = state.context
        if step >= 2 and not ctx.get('goods_id'):
            return self._error('invalid_state', '缺少 goodsId，请重置流程后重试。', state)
        if step >= 2 and 'hash_key' not in ctx:
            return self._error('invalid_state', '缺少 hashKey，请重置流程后重试。', state)
        if step >= 3 and not ctx.get('order_no'):
            return self._error('invalid_state', '缺少 orderNo，请重置流程后重试。', state)
        return None

    def _sync_remote_order_state(
        self,
        state: ProcessState,
        client: HaierClient,
        *,
        fail_on_unavailable: bool = True,
    ) -> Optional[Dict[str, Any]]:
        order_no = str(state.context.get('order_no') or '').strip()
        if not order_no:
            return None

        detail_res = client.order_detail(order_no)
        if not detail_res.get('ok'):
            if fail_on_unavailable:
                return self._error(
                    'order_sync_failed',
                    detail_res.get('msg') or '暂时无法同步订单状态，请稍后重试。',
                    state,
                    detail_res.get('raw'),
                )
            return self._error(
                'order_sync_failed',
                detail_res.get('msg') or '暂时无法同步订单状态，请稍后重试。',
                state,
                detail_res.get('raw'),
            )

        detail = detail_res.get('data') or {}
        classification = self._classify_order_detail(detail)
        if classification == 'closed':
            state.terminated = True
            state.completed = False
            state.blocked_reason = '订单已关闭或失效，流程无法继续。'
            return self._error('process_blocked', state.blocked_reason, state, detail_res.get('raw'))
        if classification == 'manual_check_required':
            state.terminated = True
            state.completed = False
            state.blocked_reason = '当前订单进入门店详情下单的待验证阶段，不能继续按扫码下单流程推进。'
            return self._error('unsupported_order_stage', state.blocked_reason, state, detail_res.get('raw'))
        if classification in {'running', 'completed'}:
            state.completed = True
            state.terminated = False
            state.current_step = 6
            state.blocked_reason = None
            return self._success(state, '订单已支付并启动，流程已自动完成。', detail_res.get('raw'))
        if classification == 'pending':
            self._apply_pending_detail_to_state(state, detail)
            return None
        self._hydrate_context_from_detail(state, detail)
        state.blocked_reason = None
        return None

    def _hydrate_context_from_detail(self, state: ProcessState, detail: Dict[str, Any]) -> None:
        if not detail:
            return
        order_item = (detail.get('orderItemList') or [{}])[0]
        goods_id = order_item.get('goodsId') or detail.get('goodsId')
        if goods_id and not state.context.get('goods_id'):
            state.context['goods_id'] = str(goods_id)
        if 'hash_key' not in state.context:
            state.context['hash_key'] = ''

    def _resolve_pending_step(self, detail: Dict[str, Any]) -> int:
        if self._is_final_pending_stage(detail):
            return 4
        return 3

    def _is_final_pending_stage(self, detail: Dict[str, Any]) -> bool:
        if self._classify_order_detail(detail) != 'pending':
            return False
        page_code = str(detail.get('pageCode') or '')
        can_pay = bool((detail.get('buttonSwitch') or {}).get('canPay'))
        return can_pay or page_code == 'waiting_choose_ump'

    def _is_manual_check_stage(self, detail: Dict[str, Any]) -> bool:
        page_code = str(detail.get('pageCode') or '')
        state_desc = str(detail.get('stateDesc') or '')
        return page_code == 'waiting_check' or '待验证' in state_desc

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

    def _advance_after_create_order(
        self,
        state: ProcessState,
        client: HaierClient,
        *,
        debug: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        order_no = str(state.context.get('order_no') or '').strip()
        if not order_no:
            return self._error('invalid_state', '缺少 orderNo，请重新开始流程。', state)

        debug_payload = dict(debug or {})
        detail_res = client.order_detail(order_no)
        debug_payload['orderDetail'] = detail_res.get('raw')
        if not detail_res.get('ok'):
            return self._error_result(detail_res, state)

        detail = self._settle_pending_order_detail(client, order_no, detail_res.get('data') or {})
        debug_payload['settledDetail'] = detail
        classification = self._classify_order_detail(detail)
        self._hydrate_context_from_detail(state, detail)

        if classification == 'closed':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '订单已关闭或失效，流程无法继续。'
            return self._error('process_blocked', state.blocked_reason, state, debug_payload)

        if classification == 'manual_check_required':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '当前订单进入门店详情下单的待验证阶段，不能继续按扫码下单流程推进。'
            return self._error('unsupported_order_stage', state.blocked_reason, state, debug_payload)

        if classification in {'running', 'completed'}:
            state.completed = True
            state.terminated = False
            state.current_step = 6
            state.blocked_reason = None
            return self._success(state, '订单已支付并启动，流程已自动完成。', debug_payload)

        if classification != 'pending':
            return self._error('order_not_ready', '新建订单后未能进入可继续状态，请稍后重试。', state, debug_payload)

        self._apply_pending_detail_to_state(state, detail)
        success_msg = '步骤 2 完成：订单已创建，请继续确认放衣。'
        if state.current_step >= 4:
            success_msg = '步骤 2 完成：订单已创建，并已进入待付款状态。'
        return self._success(state, success_msg, debug_payload)

    def _advance_to_payment_stage(
        self,
        state: ProcessState,
        client: HaierClient,
        *,
        success_msg: str,
        debug: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        order_no = str(state.context.get('order_no') or '').strip()
        if not order_no:
            return self._error('invalid_state', '缺少 orderNo，请重新开始流程。', state)

        debug_payload = dict(debug or {})
        detail: Dict[str, Any] = {}
        place_res = client.place_clothes(order_no)
        debug_payload['placeClothes'] = place_res.get('raw')
        if not place_res.get('ok'):
            detail_res = client.order_detail(order_no)
            debug_payload['orderDetail'] = detail_res.get('raw')
            if detail_res.get('ok'):
                detail = self._settle_pending_order_detail(client, order_no, detail_res.get('data') or {})
                debug_payload['settledDetail'] = detail
                if self._classify_order_detail(detail) == 'pending' and self._is_final_pending_stage(detail):
                    self._hydrate_context_from_detail(state, detail)
                    state.current_step = 4
                    state.completed = False
                    state.terminated = False
                    state.blocked_reason = None
                    return self._success(state, success_msg, debug_payload)
            return self._error_result(place_res, state)

        detail_res = client.order_detail(order_no)
        debug_payload['orderDetail'] = detail_res.get('raw')
        if not detail_res.get('ok'):
            return self._error_result(detail_res, state)

        detail = self._settle_pending_order_detail(client, order_no, detail_res.get('data') or {})
        debug_payload['settledDetail'] = detail
        classification = self._classify_order_detail(detail)
        self._hydrate_context_from_detail(state, detail)

        if classification == 'closed':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '订单已关闭或失效，流程无法继续。'
            return self._error('process_blocked', state.blocked_reason, state, debug_payload)

        if classification == 'manual_check_required':
            state.completed = False
            state.terminated = True
            state.blocked_reason = '当前订单进入门店详情下单的待验证阶段，不能继续按扫码下单流程推进。'
            return self._error('unsupported_order_stage', state.blocked_reason, state, debug_payload)

        if classification in {'running', 'completed'}:
            state.completed = True
            state.terminated = False
            state.current_step = 6
            state.blocked_reason = None
            return self._success(state, '订单已支付并启动，流程已自动完成。', debug_payload)

        if classification != 'pending' or not self._is_final_pending_stage(detail):
            return self._error('order_not_ready', '订单未能进入最终待付款状态，请稍后重试。', state, debug_payload)

        state.current_step = 4
        state.completed = False
        state.terminated = False
        state.blocked_reason = None
        return self._success(state, success_msg, debug_payload)

    def _apply_pending_detail_to_state(self, state: ProcessState, detail: Dict[str, Any]) -> None:
        self._hydrate_context_from_detail(state, detail)
        desired_step = self._resolve_pending_step(detail)
        current_step = state.current_step if 1 <= state.current_step <= 5 else 3
        state.current_step = max(3, current_step, desired_step)
        state.completed = False
        state.terminated = False
        state.blocked_reason = None

    def _classify_order_detail(self, detail: Dict[str, Any]) -> str:
        state = int(detail.get('state') or 0)
        state_desc = str(detail.get('stateDesc') or '')
        page_code = str(detail.get('pageCode') or '')
        buttons = detail.get('buttonSwitch') or {}
        can_pay = bool(buttons.get('canPay'))
        can_cancel = bool(buttons.get('canCancel'))
        can_close = bool(buttons.get('canCloseOrder'))

        if state in COMPLETED_ORDER_STATES or '已完成' in state_desc or '完成' in state_desc:
            return 'completed'
        if state in RUNNING_ORDER_STATES or '运行中' in state_desc or '洗涤中' in state_desc or '烘干中' in state_desc or '脱水中' in state_desc:
            return 'running'
        if state in CLOSED_ORDER_STATES or '关闭' in state_desc or '取消' in state_desc or '失效' in state_desc:
            return 'closed'
        if self._is_manual_check_stage(detail):
            return 'manual_check_required'
        if state in PENDING_ORDER_STATES or can_pay or can_cancel or can_close or page_code in ACTIVE_PAGE_CODES:
            return 'pending'
        return 'unknown'

    def _normalize_order_summary(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        order_item = (detail.get('orderItemList') or [{}])[0]
        buttons = detail.get('buttonSwitch') or {}
        fulfill_info = detail.get('fulfillInfo') or {}
        fulfilling_item = fulfill_info.get('fulfillingItem') or {}
        return {
            'orderNo': detail.get('orderNo'),
            'state': detail.get('state'),
            'stateDesc': detail.get('stateDesc') or '',
            'pageCode': detail.get('pageCode') or '',
            'machineName': order_item.get('goodsName') or detail.get('deviceName') or '',
            'modeName': order_item.get('goodsItemName') or '',
            'createTime': detail.get('createTime'),
            'payTime': detail.get('payTime'),
            'completeTime': detail.get('completeTime'),
            'finishTime': fulfilling_item.get('finishTime') or order_item.get('finishTime') or detail.get('finishTime'),
            'invalidTime': detail.get('invalidTime'),
            'buttonSwitch': {
                'canCancel': bool(buttons.get('canCancel')),
                'canCloseOrder': bool(buttons.get('canCloseOrder')),
                'canPay': bool(buttons.get('canPay')),
            },
        }

    def _step_scan(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        res = client.scan_goods(state.qr_code)
        if not res.get('ok'):
            return self._error_result(res, state)
        data = res.get('data') or {}
        goods_id = data.get('goodsId')
        if not goods_id:
            return self._error('invalid_response', '扫描机器成功，但未返回 goodsId。', state, res.get('raw'))
        state.context['goods_id'] = goods_id
        state.context['hash_key'] = data.get('activityHashKey', '')
        state.current_step = 2
        return self._success(state, '步骤 1 完成：已解析机器并取得临时参数。', res.get('raw'))

    def _step_create_order(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        goods_detail_res = client.goods_details(str(state.context['goods_id']))
        if not goods_detail_res.get('ok'):
            return self._error_result(goods_detail_res, state)

        goods_detail = goods_detail_res.get('data') or {}
        if not isinstance(goods_detail, dict) or not goods_detail:
            return self._error('invalid_response', '读取设备详情成功，但返回数据为空。', state, goods_detail_res.get('raw'))

        res = client.create_order(
            state.context['goods_id'],
            state.mode_id,
            state.context.get('hash_key', ''),
            goods_detail=goods_detail,
        )
        if not res.get('ok'):
            return self._error_result(res, state)
        data = res.get('data') or {}
        order_no = data.get('orderNo')
        if not order_no:
            return self._error('invalid_response', '创建订单成功，但未返回 orderNo。', state, res.get('raw'))
        state.context['order_no'] = str(order_no)
        return self._advance_after_create_order(
            state,
            client,
            debug={
                'goodsDetail': goods_detail_res.get('raw'),
                'createOrder': res.get('raw'),
            },
        )

    def _step_place_clothes(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        return self._advance_to_payment_stage(
            state,
            client,
            success_msg='步骤 3 完成：已确认放衣并进入待付款状态。',
        )

    def _step_prepare_payment(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        order_no = str(state.context.get('order_no') or '').strip()
        if not order_no:
            return self._error('invalid_state', '缺少 orderNo，请重新开始流程。', state)

        debug_payload: Dict[str, Any] = {}
        detail_res = client.order_detail(order_no)
        debug_payload['orderDetail'] = detail_res.get('raw')
        if not detail_res.get('ok'):
            return self._error_result(detail_res, state)

        detail = detail_res.get('data') or {}
        self._hydrate_context_from_detail(state, detail)
        goods_id = str(state.context.get('goods_id') or '').strip()
        category_code = HaierClient.extract_category_code(detail, default='')
        if not goods_id or not category_code:
            goods_detail_res = client.goods_details(goods_id)
            debug_payload['goodsDetail'] = goods_detail_res.get('raw')
            if not goods_detail_res.get('ok'):
                return self._error_result(goods_detail_res, state)
            goods_detail = goods_detail_res.get('data') or {}
            if not isinstance(goods_detail, dict) or not goods_detail:
                return self._error('invalid_response', '读取设备详情成功，但返回数据为空。', state, debug_payload)
            goods_id = str(goods_detail.get('id') or goods_detail.get('goodsId') or '').strip()
            category_code = HaierClient.extract_category_code(goods_detail)

        if not goods_id or not category_code:
            return self._error('invalid_response', '无法解析当前设备的 goodsId 或类型。', state, debug_payload)

        checkstand_res = client.checkstand(order_no)
        debug_payload['checkstand'] = checkstand_res.get('raw')
        if not checkstand_res.get('ok'):
            return self._error_result(checkstand_res, state)

        preview_res = client.underway_preview(order_no)
        debug_payload['underwayPreview'] = preview_res.get('raw')
        if not preview_res.get('ok'):
            return self._error_result(preview_res, state)

        verify_res = client.goods_verify(goods_id, category_code=category_code)
        debug_payload['goodsVerify'] = {
            'goodsId': goods_id,
            'categoryCode': category_code,
            'result': verify_res.get('raw'),
        }
        if not verify_res.get('ok'):
            return self._error_result(verify_res, state)

        create_res = client.create_underway(order_no)
        debug_payload['underwayCreate'] = create_res.get('raw')
        if not create_res.get('ok'):
            return self._error_result(create_res, state)
        prepay_res = self._refresh_prepay_param(state, client, allow_create_underway_fallback=False)
        if prepay_res.get('status') == 'error':
            merged_debug = dict(debug_payload)
            merged_debug['prePay'] = prepay_res.get('debug')
            prepay_res['debug'] = merged_debug
            return prepay_res
        state.current_step = 5
        debug_payload['prePay'] = prepay_res.get('debug')
        return self._success(
            state,
            '步骤 4 完成：已生成预支付参数。',
            debug_payload,
        )

    def _step_pay(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        debug_payload: Dict[str, Any] = {}
        cached_prepay_param = str(state.context.get('prepay_param') or '').strip()

        if not cached_prepay_param:
            refresh_result = self._refresh_prepay_param(state, client, allow_create_underway_fallback=True)
            if refresh_result.get('status') == 'error':
                return refresh_result
            debug_payload['initialPrePay'] = refresh_result.get('debug')
            cached_prepay_param = str(state.context.get('prepay_param') or '').strip()

        if not cached_prepay_param:
            return self._error('invalid_state', '缺少 prepayParam，请重新生成支付参数后再试。', state, debug_payload)

        first_pay = client.pay(cached_prepay_param)
        debug_payload['firstPay'] = first_pay.get('raw')
        if first_pay.get('ok'):
            state.current_step = 6
            state.completed = True
            return self._success(state, '步骤 5 完成：支付成功，设备已启动。', debug_payload)

        retry_refresh = self._refresh_prepay_param(state, client, allow_create_underway_fallback=True)
        if retry_refresh.get('status') == 'success':
            debug_payload['refreshPrepay'] = retry_refresh.get('debug')
            second_pay = client.pay(state.context['prepay_param'])
            debug_payload['secondPay'] = second_pay.get('raw')
            if second_pay.get('ok'):
                state.current_step = 6
                state.completed = True
                return self._success(state, '步骤 5 完成：支付成功，设备已启动。', debug_payload)
            final_error = second_pay
        else:
            debug_payload['refreshPrepay'] = retry_refresh.get('debug')
            final_error = first_pay

        return self._error(
            final_error.get('error_type', 'request_failed'),
            final_error.get('msg', '支付失败'),
            state,
            debug_payload,
            code=final_error.get('code'),
            data=final_error.get('data'),
        )

    def _refresh_prepay_param(
        self,
        state: ProcessState,
        client: HaierClient,
        *,
        allow_create_underway_fallback: bool,
    ) -> Dict[str, Any]:
        prepay_res = client.prepay(state.context['order_no'])
        if not prepay_res.get('ok') and allow_create_underway_fallback:
            create_res = client.create_underway(state.context['order_no'])
            if not create_res.get('ok'):
                return self._error_result(create_res, state)
            prepay_res = client.prepay(state.context['order_no'])
        if not prepay_res.get('ok'):
            return self._error_result(prepay_res, state)

        data = prepay_res.get('data') or {}
        prepay_param = data.get('prepayParam')
        if not prepay_param:
            return self._error('invalid_response', '预支付成功，但未返回 prepayParam。', state, prepay_res.get('raw'))
        state.context['prepay_param'] = prepay_param
        return {'status': 'success', 'debug': prepay_res.get('raw')}

    def _success(self, state: ProcessState, msg: str, debug: Any = None) -> Dict[str, Any]:
        return {
            'status': 'success',
            'msg': msg,
            'process': state.to_dict(),
            'debug': debug,
        }

    def _error_result(self, result: Dict[str, Any], state: Optional[ProcessState] = None) -> Dict[str, Any]:
        return self._error(
            result.get('error_type', 'request_failed'),
            result.get('msg', '请求失败'),
            state,
            result.get('raw'),
            code=result.get('code'),
            data=result.get('data'),
        )

    def _error(
        self,
        error_type: str,
        msg: str,
        state: Optional[ProcessState] = None,
        debug: Any = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            'status': 'error',
            'errorType': error_type,
            'msg': msg,
            'debug': debug,
        }
        if state:
            payload['process'] = state.to_dict()
        payload.update(extra)
        return payload

    def _build_start_message(self, cleanup_result: Dict[str, Any]) -> str:
        cleaned_count = len(cleanup_result.get('cleanedOrders') or [])
        if cleaned_count:
            return f'流程已创建，并自动结束了 {cleaned_count} 笔该机器的遗留订单。'
        return '流程已创建。'

    def _compact_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        order_no = str(order.get('orderNo') or '').strip()
        return {
            'orderNo': order_no,
            'projectName': order.get('projectName', '未知项目'),
            'stateDesc': order.get('stateDesc', '未知状态'),
        }

    def _build_machine_identifiers(self, qr_code: str, scan_data: Dict[str, Any]) -> Set[str]:
        identifiers = {str(qr_code).strip()}
        identifiers.update(self._collect_keyed_values(scan_data, MACHINE_IDENTIFIER_KEYS))
        return {item for item in identifiers if item}

    def _collect_keyed_values(self, obj: Any, keys: Set[str]) -> Set[str]:
        found: Set[str] = set()
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys and value not in (None, ''):
                    found.add(str(value).strip())
                found.update(self._collect_keyed_values(value, keys))
        elif isinstance(obj, list):
            for item in obj:
                found.update(self._collect_keyed_values(item, keys))
        return {item for item in found if item}

    def _order_matches_machine(self, order: Dict[str, Any], machine_identifiers: Set[str]) -> bool:
        order_identifiers = self._collect_keyed_values(order, MACHINE_IDENTIFIER_KEYS)
        if machine_identifiers & order_identifiers:
            return True

        order_blob = json.dumps(order, ensure_ascii=False, sort_keys=True)
        return any(identifier and len(identifier) >= 6 and identifier in order_blob for identifier in machine_identifiers)

    def _is_safe_to_auto_finish(self, order: Dict[str, Any]) -> bool:
        state_desc = str(order.get('stateDesc') or '')
        if not state_desc:
            return True
        if any(keyword in state_desc for keyword in AUTO_CLEANUP_BLOCK_KEYWORDS):
            return False
        if any(keyword in state_desc for keyword in AUTO_CLEANUP_ALLOW_KEYWORDS):
            return True
        return False
