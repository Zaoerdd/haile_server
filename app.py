from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import ALLOW_REMOTE, BASE_PATH, DEFAULT_LAT, DEFAULT_LNG, HOST, PORT, SECRET_KEY, SSL_VERIFY, load_machines, normalize_base_path, save_machines
from services.haier_client import HaierClient
from services.reservation_service import reservation_service
from services.scheduler import reservation_scheduler
from services.settings_store import settings_store
from services.workflow import WorkflowManager

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
if BASE_PATH:
    app.config['APPLICATION_ROOT'] = BASE_PATH
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1, x_prefix=1)

workflow_manager = WorkflowManager()
reservation_scheduler.update_interval(settings_store.get_effective_settings().reservation_poll_interval_seconds)
reservation_scheduler.start()

ROOM_MACHINE_CACHE_TTL_SECONDS = 20
FAVORITE_STATUS_CACHE_TTL_SECONDS = 12

room_machine_cache: Dict[str, Dict[str, Any]] = {}
favorite_status_cache: Dict[str, Dict[str, Any]] = {}
cache_lock = threading.Lock()

try:
    REMOTE_MACHINE_TIMEZONE = ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError:
    REMOTE_MACHINE_TIMEZONE = timezone(timedelta(hours=8))


def get_base_path() -> str:
    return (
        normalize_base_path(request.headers.get('X-Forwarded-Prefix'))
        or normalize_base_path(request.script_root)
        or BASE_PATH
    )


def prefix_local_path(path: str, base_path: str) -> str:
    if not base_path or not path.startswith('/'):
        return path
    if path == base_path or path.startswith(f'{base_path}/'):
        return path
    return f'{base_path}{path}'


def json_error(msg: str, error_type: str = 'request_failed', status_code: int = 400, **extra: Any):
    payload: Dict[str, Any] = {'status': 'error', 'errorType': error_type, 'msg': msg}
    payload.update(extra)
    return jsonify(payload), status_code


def build_token_missing_payload() -> Dict[str, Any]:
    return {
        'status': 'error',
        'errorType': 'token_missing',
        'msg': '未配置可用 Token，请先在设置页或 .env 中设置后再继续操作。',
    }


def resolve_token(data: Dict[str, Any] | None = None) -> str:
    request_token = ((data or {}).get('token') or '').strip()
    if request_token:
        return request_token
    return settings_store.get_effective_settings().token


def get_required_token(data: Dict[str, Any] | None = None) -> str | None:
    token = resolve_token(data)
    return token or None


def get_token_status() -> Dict[str, Any]:
    return settings_store.validate_token()


def get_location(payload: Dict[str, Any] | None = None) -> tuple[float, float]:
    source = payload or {}
    lng_raw = source.get('lng', request.args.get('lng', DEFAULT_LNG))
    lat_raw = source.get('lat', request.args.get('lat', DEFAULT_LAT))
    try:
        lng = float(lng_raw)
        lat = float(lat_raw)
    except (TypeError, ValueError):
        lng = DEFAULT_LNG
        lat = DEFAULT_LAT
    return lng, lat


def get_scan_machines() -> list[Dict[str, str]]:
    return load_machines()


def build_cache_key(*parts: Any) -> str:
    return '::'.join(str(part or '').strip() for part in parts)


def cache_get(store: Dict[str, Dict[str, Any]], key: str, ttl_seconds: int) -> Any | None:
    now = time.time()
    with cache_lock:
        entry = store.get(key)
        if not entry:
            return None
        if now - float(entry.get('updated_at') or 0) > ttl_seconds:
            store.pop(key, None)
            return None
        return deepcopy(entry.get('value'))


def cache_set(store: Dict[str, Dict[str, Any]], key: str, value: Any) -> Any:
    with cache_lock:
        store[key] = {
            'updated_at': time.time(),
            'value': deepcopy(value),
        }
    return value


def clear_favorite_status_cache(qr_code: str = '') -> None:
    normalized_qr_code = str(qr_code or '').strip()
    with cache_lock:
        if not normalized_qr_code:
            favorite_status_cache.clear()
            return
        for key in list(favorite_status_cache.keys()):
            if normalized_qr_code in key:
                favorite_status_cache.pop(key, None)


def normalize_favorite_machine_payload(payload: Dict[str, Any] | None) -> Dict[str, str]:
    source = payload or {}
    qr_code = str(source.get('qrCode') or source.get('code') or '').strip()
    label = str(source.get('label') or source.get('name') or '').strip() or qr_code
    return {
        'label': label,
        'qrCode': qr_code,
        'goodsId': str(source.get('goodsId') or source.get('id') or '').strip(),
        'shopId': str(source.get('shopId') or '').strip(),
        'shopName': str(source.get('shopName') or '').strip(),
        'categoryCode': str(source.get('categoryCode') or '').strip(),
        'categoryName': str(source.get('categoryName') or '').strip(),
        'addedAt': str(source.get('addedAt') or '').strip(),
    }


def build_scan_mapping(favorite: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not favorite:
        return None
    normalized = normalize_favorite_machine_payload(favorite)
    if not normalized.get('qrCode'):
        return None
    return normalized


def find_scan_mapping(
    machine_name: str = '',
    machine_code: str | None = None,
    goods_id: str | None = None,
) -> Dict[str, str] | None:
    normalized_name = str(machine_name or '').strip()
    normalized_code = str(machine_code or '').strip()
    normalized_goods_id = str(goods_id or '').strip()

    for favorite in load_machines():
        qr_code = str(favorite.get('qrCode') or '').strip()
        label = str(favorite.get('label') or '').strip()
        favorite_goods_id = str(favorite.get('goodsId') or '').strip()
        if normalized_code and qr_code == normalized_code:
            return build_scan_mapping(favorite)
        if normalized_goods_id and favorite_goods_id and favorite_goods_id == normalized_goods_id:
            return build_scan_mapping(favorite)
        if normalized_name and label and label == normalized_name:
            return build_scan_mapping(favorite)
    return None


def upsert_scan_machine(payload: Dict[str, Any] | None) -> list[Dict[str, str]]:
    favorite = normalize_favorite_machine_payload(payload)
    qr_code = favorite.get('qrCode')
    if not qr_code:
        raise ValueError('缺少 qrCode')

    favorites = [build_scan_mapping(item) for item in load_machines()]
    updated: list[Dict[str, str]] = []
    replaced = False
    for current in favorites:
        if not current:
            continue
        if current.get('qrCode') == qr_code:
            favorite['addedAt'] = favorite.get('addedAt') or str(current.get('addedAt') or '').strip()
            updated.append(favorite)
            replaced = True
            continue
        updated.append(current)

    if not favorite.get('addedAt'):
        favorite['addedAt'] = datetime.now().astimezone().isoformat(timespec='seconds')
    if not replaced:
        updated.append(favorite)
    clear_favorite_status_cache(qr_code)
    return save_machines(updated)


def remove_scan_machine(qr_code: str) -> list[Dict[str, str]]:
    normalized_qr_code = str(qr_code or '').strip()
    if not normalized_qr_code:
        raise ValueError('缺少 qrCode')
    favorites = [
        favorite
        for favorite in load_machines()
        if str(favorite.get('qrCode') or '').strip() != normalized_qr_code
    ]
    clear_favorite_status_cache(normalized_qr_code)
    return save_machines(favorites)


def normalize_mode(
    item: Dict[str, Any],
    *,
    mode_id: Any | None = None,
    label: str | None = None,
    unit: Any | None = None,
    duration: int | None = None,
    display_text: str | None = None,
) -> Dict[str, Any]:
    resolved_label = label if label is not None else str(item.get('name') or '未命名模式')
    resolved_unit = item.get('unit', '--') if unit is None else unit
    resolved_price = item.get('price', '0.00')
    payload = {
        'id': item.get('id') if mode_id is None else mode_id,
        'label': resolved_label,
        'feature': item.get('feature') or '',
        'price': resolved_price,
        'unit': resolved_unit,
        'displayText': display_text or f"{resolved_label} · {resolved_unit} 分钟 · ￥{resolved_price}",
        'goodsItemId': item.get('id'),
    }
    if duration is not None:
        payload['duration'] = duration
    return payload


def normalize_scan_modes(detail: Dict[str, Any]) -> list[Dict[str, Any]]:
    category_code = str(detail.get('categoryCode') or '')
    items = detail.get('items') or []
    modes: list[Dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict) or item.get('id') is None:
            continue

        if category_code == HaierClient.DRYER_CATEGORY_CODE:
            durations = HaierClient.extract_mode_durations(item)
            if durations:
                base_label = str(item.get('name') or '未命名模式')
                price = item.get('price', '0.00')
                for duration in durations:
                    try:
                        encoded_mode_id = HaierClient.encode_mode_selection(item.get('id'), duration)
                    except ValueError:
                        continue
                    modes.append(
                        normalize_mode(
                            item,
                            mode_id=encoded_mode_id,
                            label=f'{base_label} {duration}分钟',
                            unit=duration,
                            duration=duration,
                            display_text=f'{base_label} · {duration} 分钟 · ￥{price}',
                        )
                    )
                continue

        modes.append(normalize_mode(item))

    return modes


def normalize_room(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': str(item.get('id') or ''),
        'shopId': str(item.get('shopId') or ''),
        'name': item.get('name') or '未命名洗衣房',
        'address': item.get('address') or '',
        'distance': item.get('distance'),
        'idleCount': item.get('idleCount'),
        'reserveNum': item.get('reserveNum'),
        'enableReserve': bool(item.get('enableReserve')),
        'categoryCodeList': item.get('categoryCodeList') or [],
    }


def parse_datetime_value(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(REMOTE_MACHINE_TIMEZONE)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text.replace('Z', '+00:00')]
    if ' ' in text and 'T' not in text:
        candidates.append(text.replace(' ', 'T'))
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo:
            return dt.astimezone(REMOTE_MACHINE_TIMEZONE)
        return dt.replace(tzinfo=REMOTE_MACHINE_TIMEZONE)
    return None


def machine_now() -> datetime:
    return datetime.now(tz=REMOTE_MACHINE_TIMEZONE)


def format_finish_time_text(value: Any) -> str:
    dt = parse_datetime_value(value)
    if not dt:
        return ''
    return dt.strftime('%H:%M')


def build_machine_status(item: Dict[str, Any]) -> Dict[str, str]:
    state_code = int(item.get('state') or 0)
    state_desc = str(item.get('stateDesc') or '').strip()
    finish_dt = parse_datetime_value(item.get('finishTime'))
    finish_time_text = finish_dt.strftime('%H:%M') if finish_dt else ''
    is_running_signal = state_code in {2, 10} or any(keyword in state_desc for keyword in ('运行', '洗涤', '烘干', '脱水'))
    is_finish_time_expired = bool(finish_dt and finish_dt <= machine_now())

    if is_running_signal and is_finish_time_expired:
        return {
            'statusLabel': '空闲',
            'statusDetail': '空闲，可预约' if bool(item.get('enableReserve')) else '空闲',
            'finishTimeText': '',
        }
    if is_running_signal:
        return {
            'statusLabel': '运行中',
            'statusDetail': f'预计完成 {finish_time_text}' if finish_time_text else '运行中',
            'finishTimeText': finish_time_text,
        }
    if state_code == 1 or '空闲' in state_desc:
        return {
            'statusLabel': '空闲',
            'statusDetail': '空闲，可预约' if bool(item.get('enableReserve')) else '空闲',
            'finishTimeText': '',
        }
    if state_desc:
        return {
            'statusLabel': '不可用',
            'statusDetail': state_desc,
            'finishTimeText': finish_time_text,
        }
    return {
        'statusLabel': '不可用',
        'statusDetail': '设备暂不可用',
        'finishTimeText': finish_time_text,
    }


def normalize_machine(item: Dict[str, Any], scan_mapping: Dict[str, str] | None = None) -> Dict[str, Any]:
    state_code = int(item.get('state') or 0)
    status = build_machine_status(item)
    return {
        'goodsId': str(item.get('id') or ''),
        'deviceId': str(item.get('deviceId') or ''),
        'name': item.get('name') or '未命名设备',
        'categoryCode': str(item.get('categoryCode') or ''),
        'categoryName': str(item.get('categoryName') or ''),
        'floorCode': item.get('floorCode') or '',
        'state': state_code,
        'stateDesc': str(item.get('stateDesc') or status['statusLabel']),
        'finishTime': item.get('finishTime'),
        'finishTimeText': status['finishTimeText'],
        'statusLabel': status['statusLabel'],
        'statusDetail': status['statusDetail'],
        'enableReserve': bool(item.get('enableReserve')),
        'reserveState': item.get('reserveState'),
        'isFavorite': bool(scan_mapping),
        'supportsVirtualScan': bool(scan_mapping),
        'scanCode': scan_mapping.get('qrCode') if scan_mapping else None,
    }


def iter_nested_dicts(payload: Any):
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from iter_nested_dicts(value)
        return
    if isinstance(payload, list):
        for item in payload:
            yield from iter_nested_dicts(item)


def first_present_value(payload: Dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ''):
            return payload.get(key)
    return None


def extract_run_info_snapshot(payload: Any) -> Dict[str, Any] | None:
    candidate_keys = {
        'state': ['state', 'runState', 'status', 'deviceState', 'goodsState', 'workStatus'],
        'stateDesc': ['stateDesc', 'runStateDesc', 'statusDesc', 'deviceStateDesc', 'goodsStateDesc', 'workStatusDesc'],
        'finishTime': ['finishTime', 'endTime', 'runEndTime', 'expectFinishTime', 'expectedFinishTime', 'estimateFinishTime', 'deadTime', 'deadTimeTimestamp'],
    }

    best_snapshot: Dict[str, Any] | None = None
    best_score = -1
    for item in iter_nested_dicts(payload):
        state = first_present_value(item, candidate_keys['state'])
        state_desc = first_present_value(item, candidate_keys['stateDesc'])
        finish_time = first_present_value(item, candidate_keys['finishTime'])
        score = int(state is not None) + int(state_desc is not None) + int(finish_time is not None)
        if score <= 0 or score < best_score:
            continue
        best_score = score
        best_snapshot = {
            'state': state,
            'stateDesc': state_desc,
            'finishTime': finish_time,
        }
    return best_snapshot


def merge_machine_with_run_info(client: HaierClient, machine: Dict[str, Any]) -> Dict[str, Any]:
    goods_id = str(machine.get('goodsId') or '').strip()
    category_code = str(machine.get('categoryCode') or '').strip() or HaierClient.WASHER_CATEGORY_CODE
    if not goods_id:
        return machine

    run_info_res = client.goods_last_run_info(goods_id, category_code=category_code)
    if not run_info_res.get('ok'):
        return machine

    snapshot = extract_run_info_snapshot(run_info_res.get('data'))
    if not snapshot:
        return machine

    merged_item = {
        'state': snapshot.get('state') if snapshot.get('state') is not None else machine.get('state'),
        'stateDesc': snapshot.get('stateDesc') or machine.get('stateDesc'),
        'finishTime': snapshot.get('finishTime') or machine.get('finishTime'),
        'enableReserve': bool(machine.get('enableReserve')),
    }
    status = build_machine_status(merged_item)
    if status.get('statusLabel') != '运行中':
        return machine

    return {
        **machine,
        'state': merged_item.get('state'),
        'stateDesc': str(merged_item.get('stateDesc') or machine.get('stateDesc') or ''),
        'finishTime': merged_item.get('finishTime'),
        'finishTimeText': status.get('finishTimeText', machine.get('finishTimeText') or ''),
        'statusLabel': status.get('statusLabel', machine.get('statusLabel') or ''),
        'statusDetail': status.get('statusDetail', machine.get('statusDetail') or ''),
    }


def normalize_machine_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    scan_mapping = find_scan_mapping(
        machine_name=detail.get('name') or '',
        machine_code=detail.get('code'),
        goods_id=str(detail.get('id') or ''),
    )
    scan_code = str(detail.get('code') or '').strip() or str((scan_mapping or {}).get('qrCode') or '').strip()
    modes = [normalize_mode(item) for item in detail.get('items') or [] if isinstance(item, dict) and item.get('id') is not None]
    return {
        'goodsId': str(detail.get('id') or ''),
        'name': detail.get('name') or '未命名设备',
        'code': detail.get('code') or '',
        'categoryCode': detail.get('categoryCode') or '',
        'categoryName': detail.get('categoryName') or '',
        'shopId': str(detail.get('shopId') or ''),
        'shopName': detail.get('shopName') or '',
        'shopAddress': detail.get('shopAddress') or '',
        'deviceState': detail.get('deviceState'),
        'enableReserve': bool(detail.get('enableReserve')),
        'isFavorite': bool(scan_mapping),
        'supportsVirtualScan': bool(scan_code),
        'scanCode': scan_code or None,
        'modes': modes,
    }


def normalize_history_order(order: Dict[str, Any]) -> Dict[str, Any]:
    order_item = (order.get('orderItemList') or [{}])[0]
    return {
        'orderNo': order.get('orderNo', ''),
        'machineName': order_item.get('goodsName') or '未知设备',
        'modeName': order_item.get('goodsItemName') or '未知模式',
        'price': order.get('realPrice', '0.00'),
        'createTime': order.get('createTime'),
        'state': order.get('state'),
        'stateDesc': order.get('stateDesc') or '未知状态',
        'allowFeedback': bool(order.get('allowFeedback')),
        'completeTime': order.get('completeTime'),
        'raw': order,
    }


def extract_order_finish_time(detail: Dict[str, Any]) -> Any:
    order_item = (detail.get('orderItemList') or [{}])[0]
    fulfill_info = detail.get('fulfillInfo') or {}
    fulfilling_item = fulfill_info.get('fulfillingItem') or {}
    return fulfilling_item.get('finishTime') or order_item.get('finishTime') or detail.get('finishTime')


def normalize_order_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
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
        'finishTime': extract_order_finish_time(detail),
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
        'raw': detail,
    }


def sync_order_detail_after_action(
    token: str,
    order_no: str,
    *,
    attempts: int = 3,
    delay_seconds: float = 0.35,
    until_closed: bool = False,
    until_not_pending: bool = False,
) -> tuple[Dict[str, Any] | None, list[Any]]:
    normalized_order_no = str(order_no or '').strip()
    if not token or not normalized_order_no:
        return None, []

    client = HaierClient(token)
    last_detail: Dict[str, Any] | None = None
    debug_items: list[Any] = []
    max_attempts = max(1, int(attempts))
    for attempt in range(max_attempts):
        detail_res = client.order_detail(normalized_order_no)
        debug_items.append(detail_res.get('raw'))
        if detail_res.get('ok'):
            detail = detail_res.get('data') or {}
            if isinstance(detail, dict) and detail:
                last_detail = detail
                classification = reservation_service._classify_order_detail(detail)
                if until_closed and classification == 'closed':
                    break
                if until_not_pending and classification != 'pending':
                    break
                if not until_closed and not until_not_pending:
                    break
        if attempt < max_attempts - 1:
            time.sleep(delay_seconds)
    return last_detail, debug_items


def build_todo_payload(order_detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'status': 'todo',
        'message': '线上选机路径已完成到创建订单，后续支付链路待补齐。',
        'order': normalize_order_detail(order_detail),
        'todo': {
            'nextStep': '补齐 lockOrderCreate 后续支付、验单、支付与轮询流程',
        },
    }


def fetch_laundry_rooms(client: HaierClient, lng: float, lat: float) -> Dict[str, Any]:
    rooms_res = client.use_position_list(lng=lng, lat=lat, page=1, page_size=20)
    if not rooms_res.get('ok'):
        rooms_res = client.near_positions(lng=lng, lat=lat, page=1, page_size=20)
    return rooms_res


def normalize_position_device_category(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'categoryCode': str(item.get('categoryCode') or ''),
        'categoryName': str(item.get('categoryName') or ''),
        'total': int(item.get('total') or 0),
        'idleCount': int(item.get('idleCount') or 0),
    }


def fetch_room_machine_categories(client: HaierClient, position_id: str) -> Dict[str, Any]:
    response = client.position_device(position_id)
    if not response.get('ok'):
        return response

    categories = [
        normalize_position_device_category(item)
        for item in (response.get('data') or [])
        if isinstance(item, dict) and str(item.get('categoryCode') or '').strip()
    ]
    return {'ok': True, 'data': categories, 'raw': response.get('raw')}


def _fetch_all_room_machines_uncached(client: HaierClient, position_id: str, category_code: str | None = None, floor_code: str = '') -> Dict[str, Any]:
    normalized_category_code = str(category_code or '').strip()
    category_res = fetch_room_machine_categories(client, position_id)
    if not category_res.get('ok'):
        return category_res
    all_categories = category_res.get('data') or []
    categories = all_categories
    if normalized_category_code:
        categories = [item for item in all_categories if str(item.get('categoryCode') or '').strip() == normalized_category_code]

    if not categories:
        return {'ok': True, 'data': {'items': [], 'total': 0, 'categories': all_categories}}

    all_items: list[Dict[str, Any]] = []
    for category in categories:
        resolved_category_code = str(category.get('categoryCode') or '').strip()
        if not resolved_category_code:
            continue
        if not normalized_category_code and int(category.get('total') or 0) <= 0:
            continue

        page = 1
        fetched_count = 0
        total = None
        while total is None or fetched_count < total:
            response = client.device_detail_page(
                position_id=position_id,
                category_code=resolved_category_code,
                page=page,
                page_size=50,
                floor_code=floor_code,
            )
            if not response.get('ok'):
                return response
            data = response.get('data') or {}
            raw_items = data.get('items') or []
            category_items = [
                {
                    **item,
                    'categoryCode': item.get('categoryCode') or resolved_category_code,
                    'categoryName': item.get('categoryName') or category.get('categoryName') or '',
                }
                for item in raw_items
                if isinstance(item, dict)
            ]
            total = int(data.get('total') or 0)
            all_items.extend(category_items)
            fetched_count += len(category_items)
            if not raw_items:
                break
            page += 1

    deduped_items: list[Dict[str, Any]] = []
    seen_goods_ids: set[str] = set()
    for item in all_items:
        goods_id = str(item.get('id') or '').strip()
        dedupe_key = goods_id or json.dumps(item, ensure_ascii=False, sort_keys=True)
        if dedupe_key in seen_goods_ids:
            continue
        seen_goods_ids.add(dedupe_key)
        deduped_items.append(item)

    return {'ok': True, 'data': {'items': deduped_items, 'total': len(deduped_items), 'categories': all_categories}}


def fetch_all_room_machines(
    client: HaierClient,
    position_id: str,
    category_code: str | None = None,
    floor_code: str = '',
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    cache_key = build_cache_key('room-machines', position_id, category_code, floor_code)
    if not force_refresh:
        cached = cache_get(room_machine_cache, cache_key, ROOM_MACHINE_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
    result = _fetch_all_room_machines_uncached(client, position_id, category_code=category_code, floor_code=floor_code)
    if result.get('ok'):
        cache_set(room_machine_cache, cache_key, result)
    return result


def resolve_favorite_room(client: HaierClient, favorite: Dict[str, Any]) -> Dict[str, Any]:
    shop_id = str(favorite.get('shopId') or '').strip()
    shop_name = str(favorite.get('shopName') or '').strip()
    fallback_room = normalize_room(
        {
            'id': shop_id,
            'shopId': shop_id,
            'name': shop_name or '未命名洗衣房',
            'address': '',
            'categoryCodeList': [],
        }
    )
    if not shop_id or shop_name:
        return fallback_room

    room_res = client.position_detail(shop_id)
    if not room_res.get('ok'):
        return fallback_room

    room_data = room_res.get('data') or {}
    if isinstance(room_data, dict):
        room = normalize_room(room_data)
        if room.get('id'):
            return room
    return fallback_room


def build_scan_status_result(
    qr_code: str,
    *,
    matched: bool,
    room: Dict[str, Any] | None = None,
    machine: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        'qrCode': str(qr_code or '').strip(),
        'matched': bool(matched),
        'room': room,
        'machine': machine,
    }


def favorite_status_cache_key(favorite: Dict[str, Any]) -> str:
    normalized = normalize_favorite_machine_payload(favorite)
    return build_cache_key(
        'favorite-status',
        normalized.get('qrCode'),
        normalized.get('goodsId'),
        normalized.get('shopId'),
        normalized.get('label'),
    )


def find_favorite_machine_candidate(items: list[Dict[str, Any]], favorite: Dict[str, Any]) -> Dict[str, Any] | None:
    goods_id = str(favorite.get('goodsId') or '').strip()
    label = str(favorite.get('label') or '').strip()
    if goods_id:
        for machine_item in items:
            if str(machine_item.get('id') or '').strip() == goods_id:
                return machine_item
    if label:
        for machine_item in items:
            if str(machine_item.get('name') or '').strip() == label:
                return machine_item
    return None


def fetch_room_machines_for_favorites(
    client: HaierClient,
    shop_id: str,
    favorites: list[Dict[str, Any]],
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    category_codes = sorted(
        {
            str(item.get('categoryCode') or '').strip()
            for item in favorites
            if str(item.get('categoryCode') or '').strip()
        }
    )
    all_have_category = bool(favorites) and all(str(item.get('categoryCode') or '').strip() for item in favorites)
    if not all_have_category or not category_codes:
        return fetch_all_room_machines(client, position_id=shop_id, force_refresh=force_refresh)

    merged_items: list[Dict[str, Any]] = []
    categories: list[Dict[str, Any]] = []
    seen_goods_ids: set[str] = set()
    for category_code in category_codes:
        machines_res = fetch_all_room_machines(
            client,
            position_id=shop_id,
            category_code=category_code,
            force_refresh=force_refresh,
        )
        if not machines_res.get('ok'):
            return machines_res
        payload = machines_res.get('data') or {}
        if not categories:
            categories = payload.get('categories') or []
        for item in payload.get('items') or []:
            goods_id = str(item.get('id') or '').strip()
            dedupe_key = goods_id or json.dumps(item, ensure_ascii=False, sort_keys=True)
            if dedupe_key in seen_goods_ids:
                continue
            seen_goods_ids.add(dedupe_key)
            merged_items.append(item)

    return {
        'ok': True,
        'data': {
            'items': merged_items,
            'total': len(merged_items),
            'categories': categories,
        },
    }


def merge_machine_with_run_info_cached(
    client: HaierClient,
    machine: Dict[str, Any],
    run_info_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cache_key = build_cache_key(machine.get('goodsId'), machine.get('categoryCode'))
    if cache_key in run_info_cache:
        return deepcopy(run_info_cache[cache_key])
    merged = merge_machine_with_run_info(client, machine)
    run_info_cache[cache_key] = deepcopy(merged)
    return merged


def build_favorite_machine_from_run_info(client: HaierClient, favorite: Dict[str, Any]) -> Dict[str, Any] | None:
    goods_id = str(favorite.get('goodsId') or '').strip()
    if not goods_id:
        return None

    category_code = str(favorite.get('categoryCode') or '').strip() or HaierClient.WASHER_CATEGORY_CODE
    run_info_res = client.goods_last_run_info(goods_id, category_code=category_code)
    if not run_info_res.get('ok'):
        return None

    snapshot = extract_run_info_snapshot(run_info_res.get('data'))
    if not snapshot:
        return None

    if snapshot.get('state') is None and not snapshot.get('stateDesc') and not snapshot.get('finishTime'):
        return None

    status = build_machine_status(
        {
            'state': snapshot.get('state'),
            'stateDesc': snapshot.get('stateDesc'),
            'finishTime': snapshot.get('finishTime'),
            'enableReserve': True,
        }
    )

    qr_code = str(favorite.get('qrCode') or '').strip()
    return {
        'goodsId': goods_id,
        'deviceId': '',
        'name': str(favorite.get('label') or '').strip() or '收藏设备',
        'categoryCode': category_code,
        'categoryName': str(favorite.get('categoryName') or '').strip(),
        'floorCode': '',
        'state': int(snapshot.get('state') or 0),
        'stateDesc': str(snapshot.get('stateDesc') or status.get('statusLabel') or ''),
        'finishTime': snapshot.get('finishTime'),
        'finishTimeText': status.get('finishTimeText') or '',
        'statusLabel': status.get('statusLabel') or '',
        'statusDetail': status.get('statusDetail') or '',
        'enableReserve': True,
        'reserveState': None,
        'isFavorite': True,
        'supportsVirtualScan': bool(qr_code),
        'scanCode': qr_code or None,
    }


def build_favorite_machine_status(
    client: HaierClient,
    favorite: Dict[str, Any],
    room: Dict[str, Any],
    machine_item: Dict[str, Any],
    run_info_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    machine = normalize_machine(machine_item, build_scan_mapping(favorite))
    machine = merge_machine_with_run_info_cached(client, machine, run_info_cache)
    return build_scan_status_result(
        favorite.get('qrCode') or '',
        matched=True,
        room=room,
        machine=machine,
    )


def scan_legacy_favorites(
    client: HaierClient,
    favorites: list[Dict[str, Any]],
    *,
    lng: float,
    lat: float,
    run_info_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not favorites:
        return {'ok': True, 'items': []}

    qr_codes = {str(item.get('qrCode') or '').strip() for item in favorites if str(item.get('qrCode') or '').strip()}
    if not qr_codes:
        return {'ok': True, 'items': []}

    favorites_by_qr = {
        str(item.get('qrCode') or '').strip(): normalize_favorite_machine_payload(item)
        for item in favorites
        if str(item.get('qrCode') or '').strip()
    }
    matches: Dict[str, Dict[str, Any]] = {}

    rooms_res = fetch_laundry_rooms(client, lng, lat)
    if not rooms_res.get('ok'):
        return rooms_res

    room_items = ((rooms_res.get('data') or {}).get('items') or [])
    for room_item in room_items:
        unresolved = qr_codes.difference(matches.keys())
        if not unresolved:
            break
        room = normalize_room(room_item)
        position_id = room.get('id')
        if not position_id:
            continue
        machines_res = fetch_all_room_machines(client, position_id=position_id)
        if not machines_res.get('ok'):
            return machines_res
        for machine_item in ((machines_res.get('data') or {}).get('items') or []):
            scan_mapping = find_scan_mapping(
                machine_name=machine_item.get('name') or '',
                goods_id=str(machine_item.get('id') or ''),
            )
            qr_code = str((scan_mapping or {}).get('qrCode') or '').strip()
            if not qr_code or qr_code not in unresolved:
                continue
            favorite = favorites_by_qr.get(qr_code) or (scan_mapping or {})
            matches[qr_code] = build_favorite_machine_status(client, favorite, room, machine_item, run_info_cache)

    return {
        'ok': True,
        'items': [
            matches.get(
                str(item.get('qrCode') or '').strip(),
                build_scan_status_result(item.get('qrCode') or '', matched=False, room=None, machine=None),
            )
            for item in favorites
        ],
    }


def find_scan_machine_statuses(
    client: HaierClient,
    favorites: list[Dict[str, Any]],
    *,
    lng: float,
    lat: float,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    normalized_favorites = [normalize_favorite_machine_payload(item) for item in favorites if item]
    if not normalized_favorites:
        return {'ok': True, 'items': []}

    results_by_qr: Dict[str, Dict[str, Any]] = {}
    pending_by_shop: Dict[str, list[Dict[str, Any]]] = {}
    legacy_favorites: list[Dict[str, Any]] = []

    for favorite in normalized_favorites:
        qr_code = str(favorite.get('qrCode') or '').strip()
        if not qr_code:
            continue
        cache_key = favorite_status_cache_key(favorite)
        if not force_refresh:
            cached = cache_get(favorite_status_cache, cache_key, FAVORITE_STATUS_CACHE_TTL_SECONDS)
            if cached is not None:
                results_by_qr[qr_code] = cached
                continue
        run_info_machine = build_favorite_machine_from_run_info(client, favorite)
        if run_info_machine:
            results_by_qr[qr_code] = build_scan_status_result(
                qr_code,
                matched=True,
                room=resolve_favorite_room(client, favorite),
                machine=run_info_machine,
            )
            continue
        shop_id = str(favorite.get('shopId') or '').strip()
        if shop_id:
            pending_by_shop.setdefault(shop_id, []).append(favorite)
        else:
            legacy_favorites.append(favorite)

    run_info_cache: Dict[str, Dict[str, Any]] = {}

    for shop_id, grouped_favorites in pending_by_shop.items():
        machines_res = fetch_room_machines_for_favorites(client, shop_id, grouped_favorites, force_refresh=force_refresh)
        if not machines_res.get('ok'):
            return machines_res
        room = resolve_favorite_room(client, grouped_favorites[0])
        room_items = ((machines_res.get('data') or {}).get('items') or [])
        for favorite in grouped_favorites:
            qr_code = str(favorite.get('qrCode') or '').strip()
            candidate = find_favorite_machine_candidate(room_items, favorite)
            if candidate:
                results_by_qr[qr_code] = build_favorite_machine_status(client, favorite, room, candidate, run_info_cache)
            else:
                results_by_qr[qr_code] = build_scan_status_result(qr_code, matched=False, room=None, machine=None)

    unresolved_legacy = [
        favorite
        for favorite in legacy_favorites
        if str(favorite.get('qrCode') or '').strip() not in results_by_qr
    ]
    if unresolved_legacy:
        fallback_res = scan_legacy_favorites(client, unresolved_legacy, lng=lng, lat=lat, run_info_cache=run_info_cache)
        if not fallback_res.get('ok'):
            return fallback_res
        for item in fallback_res.get('items') or []:
            qr_code = str(item.get('qrCode') or '').strip()
            if qr_code:
                results_by_qr[qr_code] = item

    items: list[Dict[str, Any]] = []
    for favorite in normalized_favorites:
        qr_code = str(favorite.get('qrCode') or '').strip()
        if not qr_code:
            continue
        result = results_by_qr.get(qr_code) or build_scan_status_result(qr_code, matched=False, room=None, machine=None)
        items.append(result)
        cache_set(favorite_status_cache, favorite_status_cache_key(favorite), result)

    return {'ok': True, 'items': items}


def find_targeted_scan_machine_status(client: HaierClient, favorite: Dict[str, Any]) -> Dict[str, Any]:
    shop_id = str(favorite.get('shopId') or '').strip()
    if not shop_id:
        return {'ok': True, 'matched': False, 'room': None, 'machine': None}

    machines_res = fetch_all_room_machines(client, position_id=shop_id)
    if not machines_res.get('ok'):
        return machines_res

    candidate = find_favorite_machine_candidate(((machines_res.get('data') or {}).get('items') or []), favorite)

    if not candidate:
        return {'ok': True, 'matched': False, 'room': None, 'machine': None}

    room = resolve_favorite_room(client, favorite)
    machine = merge_machine_with_run_info(client, normalize_machine(candidate, build_scan_mapping(favorite)))
    return {
        'ok': True,
        'matched': True,
        'room': room,
        'machine': machine,
    }


def find_scan_machine_status(client: HaierClient, qr_code: str, *, lng: float, lat: float) -> Dict[str, Any]:
    normalized_qr_code = str(qr_code or '').strip()
    if not normalized_qr_code:
        return {'ok': False, 'msg': '缺少收藏设备编号', 'error_type': 'missing_qr_code'}

    favorite = find_scan_mapping(machine_code=normalized_qr_code)
    favorites = [favorite] if favorite else [{'qrCode': normalized_qr_code}]
    statuses_res = find_scan_machine_statuses(client, favorites, lng=lng, lat=lat)
    if not statuses_res.get('ok'):
        return statuses_res
    items = statuses_res.get('items') or []
    item = items[0] if items else build_scan_status_result(normalized_qr_code, matched=False, room=None, machine=None)
    return {
        'ok': True,
        'matched': bool(item.get('matched')),
        'room': item.get('room'),
        'machine': item.get('machine'),
    }


@app.route('/')
def index():
    base_path = get_base_path()
    return render_template(
        'index.html',
        allow_remote=ALLOW_REMOTE,
        ssl_verify=SSL_VERIFY,
        base_path=base_path,
        style_url=prefix_local_path(url_for('static', filename='style.css'), base_path),
        app_js_url=prefix_local_path(url_for('static', filename='app.js'), base_path),
    )


@app.route('/api/config', methods=['GET'])
def get_config():
    favorites = get_scan_machines()
    return jsonify(
        {
            'status': 'success',
            'security': {
                'sslVerify': SSL_VERIFY,
                'allowRemote': ALLOW_REMOTE,
                'tokenManagedByServer': True,
            },
            'tokenStatus': get_token_status(),
            'scheduler': reservation_scheduler.snapshot(),
            'favorites': favorites,
            'scanMachines': favorites,
        }
    )


@app.route('/api/settings', methods=['GET'])
def get_settings():
    settings = settings_store.get_effective_settings()
    return jsonify(
        {
            'status': 'success',
            'settings': settings.to_dict(),
            'tokenStatus': settings_store.validate_token(),
            'scheduler': reservation_scheduler.snapshot(),
        }
    )


@app.route('/api/settings', methods=['PUT'])
def update_settings():
    data = request.get_json(force=True) or {}
    try:
        settings = settings_store.update_settings(data)
    except ValueError as exc:
        return json_error(str(exc), error_type='invalid_settings')
    reservation_scheduler.update_interval(settings.reservation_poll_interval_seconds)
    return jsonify(
        {
            'status': 'success',
            'msg': '设置已保存。',
            'settings': settings.to_dict(),
            'tokenStatus': settings_store.validate_token(settings.token),
            'scheduler': reservation_scheduler.snapshot(),
        }
    )


@app.route('/api/laundry/sections', methods=['GET'])
def laundry_sections():
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    lng, lat = get_location()
    client = HaierClient(token)
    rooms_res = fetch_laundry_rooms(client, lng, lat)
    if not rooms_res.get('ok'):
        return json_error(rooms_res.get('msg') or '获取洗衣房列表失败。', error_type=rooms_res.get('error_type', 'room_list_failed'), debug=rooms_res.get('raw'))

    rooms = [normalize_room(item) for item in ((rooms_res.get('data') or {}).get('items') or [])]
    favorites = get_scan_machines()
    return jsonify(
        {
            'status': 'success',
            'sections': [
                {'key': 'rooms', 'label': '洗衣房', 'count': len(rooms)},
                {'key': 'favorites', 'label': '收藏', 'count': len(favorites)},
            ],
            'rooms': rooms,
            'favorites': favorites,
            'scanMachines': favorites,
            'debug': {'location': {'lng': lng, 'lat': lat}},
        }
    )


@app.route('/api/laundry/rooms', methods=['GET'])
def laundry_rooms():
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    lng, lat = get_location()
    client = HaierClient(token)
    rooms_res = client.use_position_list(lng=lng, lat=lat, page=1, page_size=20)
    if not rooms_res.get('ok'):
        return json_error(rooms_res.get('msg') or '获取洗衣房列表失败。', error_type=rooms_res.get('error_type', 'room_list_failed'), debug=rooms_res.get('raw'))

    rooms = [normalize_room(item) for item in ((rooms_res.get('data') or {}).get('items') or [])]
    return jsonify({'status': 'success', 'rooms': rooms})


@app.route('/api/laundry/rooms/<position_id>/machines', methods=['GET'])
def room_machines(position_id: str):
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    category_code = str(request.args.get('categoryCode', '') or '').strip() or None
    floor_code = request.args.get('floorCode', '')
    force_refresh = str(request.args.get('force', '') or '').strip() in {'1', 'true', 'yes'}
    client = HaierClient(token)

    room_res = client.position_detail(position_id)
    machines_res = fetch_all_room_machines(
        client,
        position_id=position_id,
        category_code=category_code,
        floor_code=floor_code,
        force_refresh=force_refresh,
    )

    if not room_res.get('ok'):
        return json_error(room_res.get('msg') or '获取洗衣房详情失败。', error_type=room_res.get('error_type', 'room_detail_failed'), debug=room_res.get('raw'))
    if not machines_res.get('ok'):
        return json_error(machines_res.get('msg') or '获取机器列表失败。', error_type=machines_res.get('error_type', 'machine_list_failed'), debug=machines_res.get('raw'))

    room = normalize_room(room_res.get('data') or {})
    machines = [
        normalize_machine(
            item,
            find_scan_mapping(
                machine_name=item.get('name') or '',
                goods_id=str(item.get('id') or ''),
            ),
        )
        for item in ((machines_res.get('data') or {}).get('items') or [])
    ]
    return jsonify(
        {
            'status': 'success',
            'room': room,
            'floors': [],
            'categories': (machines_res.get('data') or {}).get('categories') or [],
            'machines': machines,
            'selectedCategoryCode': category_code or '',
        }
    )


@app.route('/api/laundry/favorites/statuses', methods=['GET'])
def favorite_machine_statuses():
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    favorites = get_scan_machines()
    if not favorites:
        return jsonify({'status': 'success', 'items': [], 'statusesByQrCode': {}})

    lng, lat = get_location()
    force_refresh = str(request.args.get('force', '') or '').strip() in {'1', 'true', 'yes'}
    client = HaierClient(token)
    statuses_res = find_scan_machine_statuses(client, favorites, lng=lng, lat=lat, force_refresh=force_refresh)
    if not statuses_res.get('ok'):
        return json_error(
            statuses_res.get('msg') or '获取收藏设备状态失败。',
            error_type=statuses_res.get('error_type', 'favorite_statuses_failed'),
            debug=statuses_res.get('raw'),
        )

    items = statuses_res.get('items') or []
    return jsonify(
        {
            'status': 'success',
            'items': items,
            'statusesByQrCode': {
                str(item.get('qrCode') or '').strip(): {
                    'matched': bool(item.get('matched')),
                    'room': item.get('room'),
                    'machine': item.get('machine'),
                }
                for item in items
                if str(item.get('qrCode') or '').strip()
            },
        }
    )


@app.route('/api/laundry/scan-machines/<qr_code>/status', methods=['GET'])
def scan_machine_status(qr_code: str):
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    lng, lat = get_location()
    client = HaierClient(token)
    status_res = find_scan_machine_status(client, qr_code, lng=lng, lat=lat)
    if not status_res.get('ok'):
        return json_error(
            status_res.get('msg') or '获取收藏设备状态失败。',
            error_type=status_res.get('error_type', 'scan_machine_status_failed'),
            debug=status_res.get('raw'),
        )

    return jsonify(
        {
            'status': 'success',
            'matched': bool(status_res.get('matched')),
            'room': status_res.get('room'),
            'machine': status_res.get('machine'),
        }
    )


@app.route('/api/laundry/favorites', methods=['POST'])
def add_favorite_machine():
    data = request.get_json(force=True) or {}
    try:
        favorites = upsert_scan_machine(data)
    except ValueError as exc:
        return json_error(str(exc), error_type='invalid_favorite')
    except OSError as exc:
        return json_error(f'收藏写入失败：{exc}', error_type='favorite_store_write_failed', status_code=500)
    return jsonify(
        {
            'status': 'success',
            'msg': '已加入收藏。',
            'favorites': favorites,
            'scanMachines': favorites,
        }
    )


@app.route('/api/laundry/favorites/<qr_code>', methods=['DELETE'])
def delete_favorite_machine(qr_code: str):
    try:
        favorites = remove_scan_machine(qr_code)
    except ValueError as exc:
        return json_error(str(exc), error_type='invalid_favorite')
    except OSError as exc:
        return json_error(f'收藏写入失败：{exc}', error_type='favorite_store_write_failed', status_code=500)
    return jsonify(
        {
            'status': 'success',
            'msg': '已取消收藏。',
            'favorites': favorites,
            'scanMachines': favorites,
        }
    )


@app.route('/api/laundry/machines/<goods_id>', methods=['GET'])
def machine_detail(goods_id: str):
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    detail_res = client.goods_details(goods_id)
    if not detail_res.get('ok'):
        return json_error(detail_res.get('msg') or '获取机器详情失败。', error_type=detail_res.get('error_type', 'machine_detail_failed'), debug=detail_res.get('raw'))

    detail = normalize_machine_detail(detail_res.get('data') or {})
    return jsonify({'status': 'success', 'machine': detail, 'debug': detail_res.get('raw')})


@app.route('/api/orders/create-by-lock', methods=['POST'])
def create_order_by_lock():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    goods_id = data.get('goodsId')
    mode_id = data.get('modeId')
    if not goods_id or mode_id in (None, ''):
        return json_error('缺少 goodsId 或 modeId', error_type='missing_params')

    client = HaierClient(token)
    create_res = client.create_lock_order(
        goods_id=str(goods_id),
        mode_id=int(mode_id),
        hash_key=str(data.get('hashKey') or ''),
        reserve_method=data.get('reserveMethod'),
    )
    if not create_res.get('ok'):
        return json_error(create_res.get('msg') or '线上选机创建订单失败。', error_type=create_res.get('error_type', 'lock_order_failed'), debug=create_res.get('raw'))

    order_no = str((create_res.get('data') or {}).get('orderNo') or '').strip()
    detail_res = client.order_detail(order_no) if order_no else {'ok': False, 'msg': '未返回 orderNo'}
    if not detail_res.get('ok'):
        return json_error(detail_res.get('msg') or '读取订单详情失败。', error_type=detail_res.get('error_type', 'order_detail_failed'), debug=detail_res.get('raw'))

    return jsonify(
        {
            'status': 'success',
            'msg': '线上选机订单已创建，后续支付链路待补齐。',
            'result': build_todo_payload(detail_res.get('data') or {}),
            'debug': {'create': create_res.get('raw'), 'detail': detail_res.get('raw')},
        }
    )


@app.route('/api/orders/create-by-scan', methods=['POST'])
def create_order_by_scan():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    qr_code = data.get('qrCode') or data.get('qr_code')
    mode_id = data.get('modeId') or data.get('mode_id')
    if not qr_code or mode_id in (None, ''):
        return json_error('缺少 qrCode 或 modeId', error_type='missing_params')

    try:
        result = workflow_manager.run_full_process(token=token, qr_code=str(qr_code), mode_id=int(mode_id))
    except ValueError:
        return json_error('模式编号无效', error_type='invalid_mode')

    if result.get('status') != 'success':
        return jsonify(result), 400

    order_no = (((result.get('process') or {}).get('contextSummary') or {}).get('orderNo') or '').strip()
    order_detail_payload = None
    if order_no:
        detail_res = HaierClient(token).order_detail(order_no)
        if detail_res.get('ok'):
            order_detail_payload = normalize_order_detail(detail_res.get('data') or {})

    return jsonify(
        {
            'status': 'success',
            'msg': '虚拟扫码下单与支付流程已完成。',
            'process': result.get('process'),
            'cleanup': result.get('cleanup'),
            'order': order_detail_payload,
            'debug': result.get('debug'),
        }
    )


@app.route('/api/orders/<order_no>', methods=['GET'])
def order_detail(order_no: str):
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400

    res = HaierClient(token).order_detail(order_no)
    if not res.get('ok'):
        return json_error(res.get('msg') or '读取订单详情失败。', error_type=res.get('error_type', 'order_detail_failed'), debug=res.get('raw'))
    return jsonify({'status': 'success', 'order': normalize_order_detail(res.get('data') or {}), 'debug': res.get('raw')})


@app.route('/api/orders/<order_no>/cancel', methods=['POST'])
def cancel_order(order_no: str):
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    res = client.cancel_order(order_no)
    if not res.get('ok'):
        synced_detail, detail_debug = sync_order_detail_after_action(token, order_no, until_closed=True, until_not_pending=True)
        if synced_detail and reservation_service._classify_order_detail(synced_detail) == 'closed':
            reservation_service.handle_manual_order_closed(order_no, '取消', synced_detail)
            workflow_manager.sync_process_for_order(token, order_no)
            return jsonify(
                {
                    'status': 'success',
                    'msg': '订单已取消。',
                    'order': normalize_order_detail(synced_detail),
                    'debug': {
                        'action': res.get('raw'),
                        'orderDetail': detail_debug,
                    },
                }
            )
        return json_error(
            res.get('msg') or '取消订单失败。',
            error_type=res.get('error_type', 'order_cancel_failed'),
            debug={'action': res.get('raw'), 'orderDetail': detail_debug},
        )
    synced_detail, detail_debug = sync_order_detail_after_action(token, order_no, until_not_pending=True)
    reservation_service.handle_manual_order_closed(order_no, '取消', synced_detail)
    workflow_manager.sync_process_for_order(token, order_no)
    payload: Dict[str, Any] = {
        'status': 'success',
        'msg': '订单已取消。',
        'debug': {
            'action': res.get('raw'),
            'orderDetail': detail_debug,
        },
    }
    if synced_detail:
        payload['order'] = normalize_order_detail(synced_detail)
    return jsonify(payload)


@app.route('/api/orders/<order_no>/finish', methods=['POST'])
def finish_order(order_no: str):
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    res = client.finish_order(order_no)
    if not res.get('ok'):
        synced_detail, detail_debug = sync_order_detail_after_action(token, order_no, until_closed=True, until_not_pending=True)
        if synced_detail and reservation_service._classify_order_detail(synced_detail) in {'completed', 'closed'}:
            reservation_service.handle_manual_order_closed(order_no, '结束', synced_detail)
            workflow_manager.sync_process_for_order(token, order_no)
            return jsonify(
                {
                    'status': 'success',
                    'msg': '订单已结束。',
                    'order': normalize_order_detail(synced_detail),
                    'debug': {
                        'action': res.get('raw'),
                        'orderDetail': detail_debug,
                    },
                }
            )
        return json_error(
            res.get('msg') or '结束订单失败。',
            error_type=res.get('error_type', 'order_finish_failed'),
            debug={'action': res.get('raw'), 'orderDetail': detail_debug},
        )
    synced_detail, detail_debug = sync_order_detail_after_action(token, order_no, until_not_pending=True)
    reservation_service.handle_manual_order_closed(order_no, '结束', synced_detail)
    workflow_manager.sync_process_for_order(token, order_no)
    payload: Dict[str, Any] = {
        'status': 'success',
        'msg': '订单已结束。',
        'debug': {
            'action': res.get('raw'),
            'orderDetail': detail_debug,
        },
    }
    if synced_detail:
        payload['order'] = normalize_order_detail(synced_detail)
    return jsonify(payload)


@app.route('/api/orders/history', methods=['POST'])
def order_history():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    try:
        page = int(data.get('page', 1))
        page_size = int(data.get('pageSize', 10))
    except (TypeError, ValueError):
        return json_error('分页参数无效', error_type='invalid_pagination')

    res = HaierClient(token).list_history_orders(page=page, page_size=page_size)
    if not res.get('ok'):
        return json_error(res.get('msg') or '获取历史订单失败。', error_type=res.get('error_type', 'history_order_failed'), debug=res.get('raw'))

    data_payload = res.get('data') or {}
    items = [normalize_history_order(item) for item in data_payload.get('items') or []]
    return jsonify(
        {
            'status': 'success',
            'page': data_payload.get('page', page),
            'pageSize': data_payload.get('pageSize', page_size),
            'total': data_payload.get('total', len(items)),
            'items': items,
            'hasMore': (data_payload.get('page', page) * data_payload.get('pageSize', page_size)) < data_payload.get('total', len(items)),
            'debug': res.get('raw'),
        }
    )


@app.route('/api/reservations', methods=['GET'])
def reservations():
    return jsonify(
        {
            'status': 'success',
            'items': reservation_service.list_tasks(),
            'scheduler': reservation_scheduler.snapshot(),
        }
    )


@app.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.get_json(force=True) or {}
    try:
        task = reservation_service.create_task(data)
    except ValueError as exc:
        return json_error(str(exc), error_type='invalid_reservation')
    reservation_scheduler.wake()
    return jsonify({'status': 'success', 'msg': '预约任务已创建。', 'task': task})


@app.route('/api/reservations/<int:task_id>/pause', methods=['POST'])
def pause_reservation(task_id: int):
    try:
        task = reservation_service.pause_task(task_id)
    except ValueError as exc:
        return json_error(str(exc), error_type='reservation_not_found', status_code=404)
    reservation_scheduler.wake()
    return jsonify({'status': 'success', 'msg': '预约任务已暂停。', 'task': task})


@app.route('/api/reservations/<int:task_id>/resume', methods=['POST'])
def resume_reservation(task_id: int):
    try:
        task = reservation_service.resume_task(task_id)
    except ValueError as exc:
        return json_error(str(exc), error_type='invalid_reservation', status_code=400)
    reservation_scheduler.wake()
    return jsonify({'status': 'success', 'msg': '预约任务已恢复。', 'task': task})


@app.route('/api/reservations/<int:task_id>', methods=['DELETE'])
def delete_reservation(task_id: int):
    try:
        result = reservation_service.delete_task(task_id)
    except ValueError as exc:
        return json_error(str(exc), error_type='reservation_not_found', status_code=404)
    reservation_scheduler.wake()
    return jsonify({'status': 'success', 'msg': '预约任务已删除。', 'task': result})


@app.route('/api/get_modes', methods=['POST'])
def get_modes():
    data = request.get_json(force=True) or {}
    qr_code = data.get('qr_code') or data.get('qrCode')
    token = get_required_token(data)
    if not qr_code:
        return json_error('缺少机器编号', error_type='missing_qr_code')
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    scan_res = client.scan_goods(str(qr_code))
    if not scan_res.get('ok'):
        return json_error(scan_res.get('msg') or '扫码失败。', error_type=scan_res.get('error_type', 'machine_scan_failed'), debug=scan_res.get('raw'))

    scan_data = scan_res.get('data') or {}
    goods_id = scan_data.get('goodsId')
    if not goods_id:
        return json_error('未能从扫描结果中提取 goodsId', error_type='invalid_response', debug=scan_res.get('raw'))

    detail_res = client.goods_details(str(goods_id))
    if not detail_res.get('ok'):
        return json_error(detail_res.get('msg') or '获取机器详情失败。', error_type=detail_res.get('error_type', 'machine_detail_failed'), debug=detail_res.get('raw'))

    detail = detail_res.get('data') or {}
    modes = normalize_scan_modes(detail if isinstance(detail, dict) else {})
    return jsonify(
        {
            'status': 'success',
            'modes': modes,
            'goodsId': goods_id,
            'categoryCode': detail.get('categoryCode') if isinstance(detail, dict) else '',
            'debug': detail_res.get('raw'),
        }
    )


@app.route('/api/process/start', methods=['POST'])
def start_process():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    qr_code = data.get('qr_code') or data.get('qrCode')
    mode_id = data.get('mode_id') or data.get('modeId')
    if not qr_code or mode_id in (None, ''):
        return json_error('缺少机器编号或模式编号', error_type='missing_params')
    if not token:
        return jsonify(build_token_missing_payload()), 400
    try:
        result = workflow_manager.start_process(token=token, qr_code=str(qr_code), mode_id=int(mode_id))
    except ValueError:
        return json_error('模式编号无效', error_type='invalid_mode')
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@app.route('/api/process/next', methods=['POST'])
def process_next():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    process_id = data.get('process_id') or data.get('processId')
    if not process_id:
        return json_error('缺少 processId', error_type='missing_process_id')
    if not token:
        return jsonify(build_token_missing_payload()), 400

    result = workflow_manager.execute_next(process_id=str(process_id), token=token)
    if result.get('status') == 'success':
        process_payload = result.get('process') or {}
        context_summary = process_payload.get('contextSummary') or {}
        order_no = str(context_summary.get('orderNo') or '').strip()
        if order_no:
            reservation_service.sync_task_order_snapshot(token, order_no)
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@app.route('/api/process/reset', methods=['POST'])
def process_reset():
    data = request.get_json(force=True) or {}
    process_id = data.get('process_id') or data.get('processId')
    token = resolve_token(data) or None
    cleanup_remote = bool(data.get('cleanup_remote', data.get('cleanupRemote', False)))
    if process_id:
        if cleanup_remote and not token:
            return jsonify(build_token_missing_payload()), 400
        result = workflow_manager.reset_process(str(process_id), token=token, cleanup_remote=cleanup_remote)
        status_code = 200 if result.get('status') == 'success' else 400
        return jsonify(result), status_code
    return jsonify({'status': 'success', 'msg': '流程已重置。'})


@app.route('/api/processes/active', methods=['GET'])
def active_processes():
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400
    items = workflow_manager.list_active_processes(token)
    return jsonify({'status': 'success', 'items': items})


@app.route('/api/processes/<process_id>', methods=['GET'])
def process_detail(process_id: str):
    token = get_required_token()
    if not token:
        return jsonify(build_token_missing_payload()), 400
    item = workflow_manager.get_process_details(process_id, token)
    if not item:
        return json_error('流程不存在', error_type='process_not_found', status_code=404)
    return jsonify({'status': 'success', 'process': item})


@app.route('/api/get_orders', methods=['POST'])
def get_underway_orders():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    res = HaierClient(token).get_underway_orders()
    if not res.get('ok'):
        return json_error(res.get('msg') or '获取进行中订单失败。', error_type=res.get('error_type', 'underway_order_failed'), debug=res.get('raw'))

    orders = res.get('data') or []
    normalized = []
    for order in orders:
        order_no = order.get('orderNo', '')
        state_desc = order.get('stateDesc', '未知状态')
        project_name = order.get('projectName', '未知项目')
        suffix = order_no[-6:] if order_no else '------'
        normalized.append(
            {
                'orderNo': order_no,
                'projectName': project_name,
                'stateDesc': state_desc,
                'displayText': f'[{state_desc}] {project_name} (...{suffix})',
                'updateTime': order.get('updateTime') or order.get('gmtModified') or 0,
                'raw': order,
            }
        )
    normalized.sort(key=lambda item: item.get('updateTime') or 0, reverse=True)
    return jsonify({'status': 'success', 'orders': normalized, 'debug': res.get('raw')})


@app.route('/api/kill_order', methods=['POST'])
def kill_order():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    order_no = data.get('order_no')
    if not order_no:
        return json_error('缺少 order_no', error_type='missing_order_no')
    if not token:
        return jsonify(build_token_missing_payload()), 400

    res = HaierClient(token).finish_order(str(order_no))
    if not res.get('ok'):
        return json_error(res.get('msg') or '强制结束订单失败。', error_type=res.get('error_type', 'order_finish_failed'), debug=res.get('raw'))
    reservation_service.handle_manual_order_closed(str(order_no), '结束')
    return jsonify({'status': 'success', 'msg': '订单已强制结束。', 'debug': res.get('raw')})


if __name__ == '__main__':
    print(f'[*] Starting server on http://{HOST}:{PORT} (allow_remote={ALLOW_REMOTE}, ssl_verify={SSL_VERIFY})')
    app.run(host=HOST, port=PORT)
