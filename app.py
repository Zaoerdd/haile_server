from __future__ import annotations

from datetime import datetime
import time
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import ALLOW_REMOTE, BASE_PATH, DEFAULT_LAT, DEFAULT_LNG, HOST, PORT, SECRET_KEY, SSL_VERIFY, load_machines, normalize_base_path
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
    machine_map = load_machines()
    return [{'label': label, 'qrCode': qr_code} for label, qr_code in machine_map.items()]


def find_scan_mapping(machine_name: str, machine_code: str | None = None) -> Dict[str, str] | None:
    machine_map = load_machines()
    if machine_name in machine_map:
        return {'label': machine_name, 'qrCode': machine_map[machine_name]}
    if machine_code:
        for label, qr_code in machine_map.items():
            if qr_code == machine_code:
                return {'label': label, 'qrCode': qr_code}
    return None


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
            return datetime.fromtimestamp(timestamp).astimezone()
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
        return dt.astimezone() if dt.tzinfo else dt.astimezone()
    return None


def format_finish_time_text(value: Any) -> str:
    dt = parse_datetime_value(value)
    if not dt:
        return ''
    return dt.strftime('%H:%M')


def build_machine_status(item: Dict[str, Any]) -> Dict[str, str]:
    state_code = int(item.get('state') or 0)
    state_desc = str(item.get('stateDesc') or '').strip()
    finish_time_text = format_finish_time_text(item.get('finishTime'))

    if state_code == 2 or any(keyword in state_desc for keyword in ('运行', '洗涤', '烘干', '脱水')):
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
        'floorCode': item.get('floorCode') or '',
        'state': state_code,
        'stateDesc': str(item.get('stateDesc') or status['statusLabel']),
        'finishTime': item.get('finishTime'),
        'finishTimeText': status['finishTimeText'],
        'statusLabel': status['statusLabel'],
        'statusDetail': status['statusDetail'],
        'enableReserve': bool(item.get('enableReserve')),
        'reserveState': item.get('reserveState'),
        'supportsVirtualScan': bool(scan_mapping),
        'scanCode': scan_mapping.get('qrCode') if scan_mapping else None,
    }


def normalize_machine_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    scan_mapping = find_scan_mapping(detail.get('name') or '', detail.get('code'))
    modes = [normalize_mode(item) for item in detail.get('items') or [] if isinstance(item, dict) and item.get('id') is not None]
    return {
        'goodsId': str(detail.get('id') or ''),
        'name': detail.get('name') or '未命名设备',
        'code': detail.get('code') or '',
        'categoryCode': detail.get('categoryCode') or '',
        'shopId': str(detail.get('shopId') or ''),
        'shopName': detail.get('shopName') or '',
        'shopAddress': detail.get('shopAddress') or '',
        'deviceState': detail.get('deviceState'),
        'enableReserve': bool(detail.get('enableReserve')),
        'supportsVirtualScan': bool(scan_mapping),
        'scanCode': scan_mapping.get('qrCode') if scan_mapping else None,
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


def fetch_all_room_machines(client: HaierClient, position_id: str, category_code: str = '00', floor_code: str = '') -> Dict[str, Any]:
    page = 1
    items: list[Dict[str, Any]] = []
    total = None
    while total is None or len(items) < total:
        response = client.device_detail_page(position_id=position_id, category_code=category_code, page=page, page_size=50, floor_code=floor_code)
        if not response.get('ok'):
            return response
        data = response.get('data') or {}
        total = int(data.get('total') or 0)
        items.extend(data.get('items') or [])
        if not data.get('items'):
            break
        page += 1
    return {'ok': True, 'data': {'items': items, 'total': total or len(items)}}


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
            'scanMachines': get_scan_machines(),
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
    rooms_res = client.use_position_list(lng=lng, lat=lat, page=1, page_size=20)
    if not rooms_res.get('ok'):
        rooms_res = client.near_positions(lng=lng, lat=lat, page=1, page_size=20)
    if not rooms_res.get('ok'):
        return json_error(rooms_res.get('msg') or '获取洗衣房列表失败。', error_type=rooms_res.get('error_type', 'room_list_failed'), debug=rooms_res.get('raw'))

    rooms = [normalize_room(item) for item in ((rooms_res.get('data') or {}).get('items') or [])]
    scan_machines = get_scan_machines()
    return jsonify(
        {
            'status': 'success',
            'sections': [
                {'key': 'rooms', 'label': '洗衣房', 'count': len(rooms)},
                {'key': 'scan-machines', 'label': '扫码机组', 'count': len(scan_machines)},
            ],
            'rooms': rooms,
            'scanMachines': scan_machines,
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

    category_code = request.args.get('categoryCode', '00')
    floor_code = request.args.get('floorCode', '')
    client = HaierClient(token)

    room_res = client.position_detail(position_id)
    floors_res = client.floor_code_list(position_id)
    machines_res = fetch_all_room_machines(client, position_id=position_id, category_code=category_code, floor_code=floor_code)

    if not room_res.get('ok'):
        return json_error(room_res.get('msg') or '获取洗衣房详情失败。', error_type=room_res.get('error_type', 'room_detail_failed'), debug=room_res.get('raw'))
    if not floors_res.get('ok'):
        return json_error(floors_res.get('msg') or '获取楼层列表失败。', error_type=floors_res.get('error_type', 'floor_list_failed'), debug=floors_res.get('raw'))
    if not machines_res.get('ok'):
        return json_error(machines_res.get('msg') or '获取机器列表失败。', error_type=machines_res.get('error_type', 'machine_list_failed'), debug=machines_res.get('raw'))

    room = normalize_room(room_res.get('data') or {})
    machines = [
        normalize_machine(item, find_scan_mapping(item.get('name') or ''))
        for item in ((machines_res.get('data') or {}).get('items') or [])
    ]
    return jsonify(
        {
            'status': 'success',
            'room': room,
            'floors': floors_res.get('data') or [],
            'machines': machines,
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
