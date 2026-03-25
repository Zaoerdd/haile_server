from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from config import PROCESS_TTL_SECONDS
from services.haier_client import HaierClient


STEP_LABELS = {
    1: '解析机器',
    2: '创建订单',
    3: '确认放衣',
    4: '生成结算单与预支付',
    5: '支付并启动',
}

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


@dataclass
class ProcessState:
    process_id: str
    qr_code: str
    mode_id: int
    current_step: int = 1
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'processId': self.process_id,
            'qrCode': self.qr_code,
            'modeId': self.mode_id,
            'currentStep': self.current_step,
            'currentStepLabel': STEP_LABELS.get(self.current_step, '已完成' if self.completed else '未知步骤'),
            'completed': self.completed,
            'contextSummary': {
                'goodsId': self.context.get('goods_id'),
                'orderNo': self.context.get('order_no'),
                'prepayReady': bool(self.context.get('prepay_param')),
            },
            'updatedAt': self.updated_at,
        }


class WorkflowManager:
    def __init__(self):
        self.processes: Dict[str, ProcessState] = {}

    def cleanup(self) -> None:
        now = time.time()
        expired = [pid for pid, p in self.processes.items() if now - p.updated_at > PROCESS_TTL_SECONDS]
        for pid in expired:
            self.processes.pop(pid, None)

    def start_process(self, token: str, qr_code: str, mode_id: int) -> Dict[str, Any]:
        self.cleanup()
        cleanup_result = self.cleanup_machine_orders(token=token, qr_code=qr_code)
        if cleanup_result.get('status') != 'success':
            return cleanup_result

        process_id = uuid.uuid4().hex
        state = ProcessState(process_id=process_id, qr_code=qr_code, mode_id=int(mode_id))
        self.processes[process_id] = state
        return {
            'status': 'success',
            'msg': self._build_start_message(cleanup_result),
            'process': state.to_dict(),
            'cleanup': cleanup_result,
            'debug': cleanup_result.get('debug'),
        }

    def reset_process(self, process_id: str, token: Optional[str] = None, cleanup_remote: bool = False) -> Dict[str, Any]:
        state = self.processes.pop(process_id, None)
        cleanup_result: Optional[Dict[str, Any]] = None

        if state and cleanup_remote and token and not state.completed and state.context.get('order_no'):
            cleanup_result = self.cleanup_order_by_no(token=token, order_no=state.context['order_no'])
            if cleanup_result.get('status') != 'success':
                return {
                    'status': 'error',
                    'msg': '本地流程已重置，但清理云端遗留订单失败，请手动强杀。',
                    'cleanup': cleanup_result,
                    'process': state.to_dict(),
                    'debug': cleanup_result.get('debug'),
                }

        msg = '流程已重置。'
        if cleanup_result and cleanup_result.get('cleanedOrders'):
            msg = f"流程已重置，并自动结束 {len(cleanup_result['cleanedOrders'])} 笔遗留订单。"

        payload: Dict[str, Any] = {'status': 'success', 'msg': msg}
        if cleanup_result:
            payload['cleanup'] = cleanup_result
            payload['debug'] = cleanup_result.get('debug')
        return payload

    def get(self, process_id: str) -> Optional[ProcessState]:
        self.cleanup()
        return self.processes.get(process_id)

    def execute_next(self, process_id: str, token: str) -> Dict[str, Any]:
        state = self.get(process_id)
        if not state:
            return self._error('process_not_found', '流程不存在或已过期，请重新开始。')
        if state.completed:
            return self._error('already_completed', '流程已经完成，无需继续执行。')

        client = HaierClient(token)
        step = state.current_step

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
        handler = handlers.get(step)
        if not handler:
            return self._error('invalid_step', f'未知步骤: {step}')
        result = handler(state, client)
        state.updated_at = time.time()
        return result

    def cleanup_machine_orders(self, token: str, qr_code: str) -> Dict[str, Any]:
        client = HaierClient(token)
        scan_res = client.scan_goods(qr_code)
        if not scan_res.get('ok'):
            return self._error(
                'machine_scan_failed',
                '创建流程前扫描机器失败，无法确认是否有遗留订单。',
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
                '创建流程前读取进行中订单失败，无法确认是否有遗留订单。',
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
                failed_orders.append({
                    **self._compact_order(order),
                    'reason': '订单缺少 orderNo，无法自动结束。',
                })
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
            msg_parts: List[str] = []
            if blocked_orders:
                msg_parts.append(f'检测到 {len(blocked_orders)} 笔同机订单，但状态疑似已启动，未自动结束')
            if failed_orders:
                msg_parts.append(f'另有 {len(failed_orders)} 笔同机遗留订单自动结束失败')
            return {
                'status': 'error',
                'errorType': 'stale_order_cleanup_failed',
                'msg': '；'.join(msg_parts) + '，请先手动检查/强杀后再试。',
                'matchedOrders': matched_orders,
                'cleanedOrders': cleaned_orders,
                'blockedOrders': blocked_orders,
                'failedOrders': failed_orders,
                'debug': debug,
            }

        msg = '未发现该机器的遗留订单。'
        if cleaned_orders:
            msg = f"已自动结束 {len(cleaned_orders)} 笔该机器的遗留订单。"
        return {
            'status': 'success',
            'msg': msg,
            'matchedOrders': matched_orders,
            'cleanedOrders': cleaned_orders,
            'blockedOrders': blocked_orders,
            'failedOrders': failed_orders,
            'debug': debug,
        }

    def cleanup_order_by_no(self, token: str, order_no: str) -> Dict[str, Any]:
        client = HaierClient(token)
        finish_res = client.finish_order(order_no)
        if not finish_res.get('ok'):
            return {
                'status': 'error',
                'errorType': finish_res.get('error_type', 'remote_cleanup_failed'),
                'msg': finish_res.get('msg') or '自动结束订单失败。',
                'matchedOrders': [{'orderNo': order_no}],
                'cleanedOrders': [],
                'blockedOrders': [],
                'failedOrders': [{'orderNo': order_no, 'reason': finish_res.get('msg') or '自动结束失败。'}],
                'debug': finish_res.get('raw'),
            }
        return {
            'status': 'success',
            'msg': '已自动结束当前流程对应的云端订单。',
            'matchedOrders': [{'orderNo': order_no}],
            'cleanedOrders': [{'orderNo': order_no}],
            'blockedOrders': [],
            'failedOrders': [],
            'debug': finish_res.get('raw'),
        }

    def _validate_preconditions(self, state: ProcessState) -> Optional[Dict[str, Any]]:
        step = state.current_step
        ctx = state.context
        if step >= 2 and not ctx.get('goods_id'):
            return self._error('invalid_state', '缺少 goods_id，请重置流程后重试。', state)
        if step >= 2 and 'hash_key' not in ctx:
            return self._error('invalid_state', '缺少 hash_key，请重置流程后重试。', state)
        if step >= 3 and not ctx.get('order_no'):
            return self._error('invalid_state', '缺少 order_no，请重置流程后重试。', state)
        if step >= 5 and not ctx.get('prepay_param'):
            return self._error('invalid_state', '缺少 prepay_param，请重置流程后重试。', state)
        return None

    def _step_scan(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        res = client.scan_goods(state.qr_code)
        if not res.get('ok'):
            return self._error_result(res, state)
        data = res.get('data') or {}
        goods_id = data.get('goodsId')
        if not goods_id:
            return self._error('invalid_response', '解析机器成功，但未返回 goodsId。', state, res.get('raw'))
        state.context['goods_id'] = goods_id
        state.context['hash_key'] = data.get('activityHashKey', '')
        state.current_step = 2
        return self._success(state, '步骤 1 完成：已解析机器并拿到临时 Key。', res.get('raw'))

    def _step_create_order(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        res = client.create_order(state.context['goods_id'], state.mode_id, state.context.get('hash_key', ''))
        if not res.get('ok'):
            return self._error_result(res, state)
        data = res.get('data') or {}
        order_no = data.get('orderNo')
        if not order_no:
            return self._error('invalid_response', '创建订单成功，但未返回 orderNo。', state, res.get('raw'))
        state.context['order_no'] = order_no
        state.current_step = 3
        return self._success(state, '步骤 2 完成：订单已创建，请放衣并关紧舱门。', res.get('raw'))

    def _step_place_clothes(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        res = client.place_clothes(state.context['order_no'])
        if not res.get('ok'):
            return self._error_result(res, state)
        state.current_step = 4
        return self._success(state, '步骤 3 完成：已发送确认放衣指令。', res.get('raw'))

    def _step_prepare_payment(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
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
        state.current_step = 5
        return self._success(
            state,
            '步骤 4 完成：结算单与预支付凭证已就绪。',
            {
                'underwayCreate': create_res.get('raw'),
                'prePay': prepay_res.get('raw'),
            },
        )

    def _step_pay(self, state: ProcessState, client: HaierClient) -> Dict[str, Any]:
        res = client.pay(state.context['prepay_param'])
        if not res.get('ok'):
            return self._error_result(res, state)
        state.current_step = 6
        state.completed = True
        return self._success(state, '步骤 5 完成：支付成功，设备已启动。', res.get('raw'))

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

    def _error(self, error_type: str, msg: str, state: Optional[ProcessState] = None, debug: Any = None, **extra: Any) -> Dict[str, Any]:
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
            return f'流程已创建，并自动结束 {cleaned_count} 笔该机器的遗留订单。'
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
        for identifier in machine_identifiers:
            if identifier and len(identifier) >= 6 and identifier in order_blob:
                return True
        return False

    def _is_safe_to_auto_finish(self, order: Dict[str, Any]) -> bool:
        state_desc = str(order.get('stateDesc') or '')
        if not state_desc:
            return True
        if any(keyword in state_desc for keyword in AUTO_CLEANUP_BLOCK_KEYWORDS):
            return False
        if any(keyword in state_desc for keyword in AUTO_CLEANUP_ALLOW_KEYWORDS):
            return True
        return False
