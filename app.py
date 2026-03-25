from __future__ import annotations

from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

from config import ALLOW_REMOTE, HOST, PORT, SECRET_KEY, SSL_VERIFY, get_haile_token, load_machines
from services.haier_client import HaierClient
from services.workflow import WorkflowManager

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY

machines = load_machines()
workflow_manager = WorkflowManager()


def build_token_missing_payload() -> Dict[str, Any]:
    return {
        'status': 'error',
        'errorType': 'token_missing',
        'msg': '未配置可用 token，请在 .env 中设置 HAILE_TOKEN 或在请求中传入 token。',
    }


def resolve_token(data: Dict[str, Any] | None = None) -> str:
    request_token = ((data or {}).get('token') or '').strip()
    return request_token or get_haile_token()


def get_required_token(data: Dict[str, Any]) -> str | None:
    token = resolve_token(data)
    if token:
        return token
    return None


def validate_config_token() -> Dict[str, Any]:
    token = get_haile_token()
    if not token:
        return {
            'source': 'env',
            'configured': False,
            'valid': False,
            'reason': 'missing',
            'message': '未在 .env 中配置 HAILE_TOKEN，请先补充后再刷新页面。',
        }

    client = HaierClient(token)
    result = client.get_orders()
    if result.get('ok'):
        return {
            'source': 'env',
            'configured': True,
            'valid': True,
            'reason': 'ok',
            'message': '已从服务器配置读取 token，校验通过。',
        }

    if result.get('error_type') == 'business':
        return {
            'source': 'env',
            'configured': True,
            'valid': False,
            'reason': 'invalid',
            'message': '配置中的 token 无效或已失效，请更新 .env 中的 HAILE_TOKEN 后刷新页面。',
        }

    detail = result.get('msg') or '请检查网络连接或稍后重试。'
    return {
        'source': 'env',
        'configured': True,
        'valid': False,
        'reason': 'check_failed',
        'message': f'暂时无法校验配置 token：{detail}',
    }


@app.route('/')
def index():
    return render_template('index.html', allow_remote=ALLOW_REMOTE, ssl_verify=SSL_VERIFY)


@app.route('/api/config', methods=['GET'])
def get_config():
    token_status = validate_config_token()
    return jsonify(
        {
            'status': 'success',
            'machines': [{'label': label, 'value': value} for label, value in machines.items()],
            'security': {
                'sslVerify': SSL_VERIFY,
                'allowRemote': ALLOW_REMOTE,
                'tokenManagedByServer': True,
                'tokenMaskedInput': False,
            },
            'tokenStatus': token_status,
        }
    )


@app.route('/api/get_modes', methods=['POST'])
def get_modes():
    data = request.get_json(force=True) or {}
    qr_code = data.get('qr_code')
    token = get_required_token(data)
    if not qr_code:
        return jsonify({'status': 'error', 'msg': '缺少机器编号'}), 400
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    scan_res = client.scan_goods(qr_code)
    if not scan_res.get('ok'):
        return jsonify({'status': 'error', 'msg': scan_res.get('msg'), 'debug': scan_res.get('raw'), 'errorType': scan_res.get('error_type')}), 400

    scan_data = scan_res.get('data') or {}
    goods_id = scan_data.get('goodsId')
    if not goods_id:
        return jsonify({'status': 'error', 'msg': '未能从扫描结果中提取 goodsId', 'debug': scan_res.get('raw')}), 400

    detail_res = client.goods_details(goods_id)
    if not detail_res.get('ok'):
        return jsonify({'status': 'error', 'msg': detail_res.get('msg'), 'debug': detail_res.get('raw'), 'errorType': detail_res.get('error_type')}), 400

    items = (detail_res.get('data') or {}).get('items', [])
    modes = []
    for item in items:
        if isinstance(item, dict) and item.get('id') is not None and item.get('name'):
            modes.append(
                {
                    'id': item['id'],
                    'label': f"{item['name']} ({item.get('unit', '--')}分钟) - ￥{item.get('price', '0.00')}",
                    'price': item.get('price', '0.00'),
                    'unit': item.get('unit', '--'),
                }
            )

    return jsonify({'status': 'success', 'modes': modes, 'goodsId': goods_id, 'debug': detail_res.get('raw')})


@app.route('/api/process/start', methods=['POST'])
def start_process():
    data = request.get_json(force=True) or {}
    qr_code = data.get('qr_code')
    mode_id = data.get('mode_id')
    token = get_required_token(data)
    if not qr_code or mode_id in (None, ''):
        return jsonify({'status': 'error', 'msg': '缺少机器编号或模式编号'}), 400
    if not token:
        return jsonify(build_token_missing_payload()), 400
    try:
        result = workflow_manager.start_process(token=token, qr_code=qr_code, mode_id=int(mode_id))
    except ValueError:
        return jsonify({'status': 'error', 'msg': '模式编号无效'}), 400

    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@app.route('/api/process/next', methods=['POST'])
def process_next():
    data = request.get_json(force=True) or {}
    process_id = data.get('process_id')
    token = get_required_token(data)
    if not process_id:
        return jsonify({'status': 'error', 'msg': '缺少 process_id'}), 400
    if not token:
        return jsonify(build_token_missing_payload()), 400

    result = workflow_manager.execute_next(process_id=process_id, token=token)
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@app.route('/api/process/reset', methods=['POST'])
def process_reset():
    data = request.get_json(force=True) or {}
    process_id = data.get('process_id')
    token = resolve_token(data) or None
    cleanup_remote = bool(data.get('cleanup_remote', False))
    if process_id:
        if cleanup_remote and not token:
            return jsonify(build_token_missing_payload()), 400
        result = workflow_manager.reset_process(process_id, token=token or None, cleanup_remote=cleanup_remote)
        status_code = 200 if result.get('status') == 'success' else 400
        return jsonify(result), status_code
    return jsonify({'status': 'success', 'msg': '流程已重置。'})


@app.route('/api/get_orders', methods=['POST'])
def get_orders():
    data = request.get_json(force=True) or {}
    token = get_required_token(data)
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    res = client.get_orders()
    if not res.get('ok'):
        return jsonify({'status': 'error', 'msg': res.get('msg'), 'debug': res.get('raw'), 'errorType': res.get('error_type')}), 400

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
        return jsonify({'status': 'error', 'msg': '缺少 order_no'}), 400
    if not token:
        return jsonify(build_token_missing_payload()), 400

    client = HaierClient(token)
    res = client.finish_order(order_no)
    if not res.get('ok'):
        return jsonify(
            {
                'status': 'error',
                'msg': res.get('msg'),
                'debug': res.get('raw'),
                'errorType': res.get('error_type'),
                'rawCode': res.get('code'),
                'rawData': res.get('data'),
            }
        ), 400

    return jsonify({'status': 'success', 'msg': '订单已强制结束。', 'debug': res.get('raw')})


if __name__ == '__main__':
    print(f'[*] Starting server on http://{HOST}:{PORT} (allow_remote={ALLOW_REMOTE}, ssl_verify={SSL_VERIFY})')
    app.run(host=HOST, port=PORT)
