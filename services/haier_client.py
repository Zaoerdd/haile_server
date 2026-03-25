from __future__ import annotations

import requests
from requests import RequestException
from typing import Any, Dict, Optional

from config import (
    BASE_URL,
    DEFAULT_APP_TYPE,
    DEFAULT_APP_VERSION,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    SSL_VERIFY,
)


class HaierClient:
    def __init__(self, token: str, timeout: float = DEFAULT_TIMEOUT, retry: int = DEFAULT_RETRY):
        self.token = token.strip()
        self.timeout = timeout
        self.retry = max(retry, 0)
        self.session = requests.Session()

    def get_headers(self) -> Dict[str, str]:
        return {
            'authorization': self.token,
            'appVersion': DEFAULT_APP_VERSION,
            'appType': DEFAULT_APP_TYPE,
            'Content-Type': 'application/json',
            'User-Agent': DEFAULT_USER_AGENT,
        }

    def _request(self, method: str, path: str, *, json: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f'{BASE_URL}{path}'
        last_error: Optional[Dict[str, Any]] = None

        for attempt in range(self.retry + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=self.get_headers(),
                    json=json,
                    params=params,
                    timeout=self.timeout,
                    verify=SSL_VERIFY,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.Timeout:
                last_error = {
                    'ok': False,
                    'error_type': 'timeout',
                    'msg': f'请求超时（>{self.timeout} 秒）',
                    'raw': None,
                }
            except ValueError:
                last_error = {
                    'ok': False,
                    'error_type': 'invalid_json',
                    'msg': '服务端返回了无法解析的 JSON',
                    'raw': None,
                }
            except RequestException as exc:
                last_error = {
                    'ok': False,
                    'error_type': 'network',
                    'msg': f'网络异常: {exc}',
                    'raw': None,
                }
            else:
                if not isinstance(payload, dict):
                    return {
                        'ok': False,
                        'error_type': 'invalid_response',
                        'msg': '服务端返回结构不是对象',
                        'raw': payload,
                    }

                code = payload.get('code')
                data = payload.get('data')
                if code != 0:
                    return {
                        'ok': False,
                        'error_type': 'business',
                        'msg': payload.get('msg') or payload.get('message') or f'接口返回失败 code={code}',
                        'code': code,
                        'data': data,
                        'raw': payload,
                    }

                return {
                    'ok': True,
                    'code': code,
                    'msg': payload.get('msg', ''),
                    'data': data,
                    'raw': payload,
                }

            if attempt < self.retry:
                continue
        return last_error or {
            'ok': False,
            'error_type': 'unknown',
            'msg': '未知请求错误',
            'raw': None,
        }

    def scan_goods(self, qr_code: str) -> Dict[str, Any]:
        return self._request('GET', '/goods/scan', params={'n': qr_code, 'backDevice': 1})

    def goods_details(self, goods_id: str) -> Dict[str, Any]:
        return self._request('GET', '/goods/normal/details', params={'goodsId': goods_id})

    def create_order(self, goods_id: str, mode_id: int, hash_key: str) -> Dict[str, Any]:
        payload = {
            'optionalInfo': {},
            'purchaseList': [
                {
                    'goodsId': str(goods_id),
                    'goodsItemId': int(mode_id),
                    'soldType': 1,
                    'amount': 1,
                    'num': 1,
                }
            ],
            'hashKey': hash_key,
        }
        return self._request('POST', '/trade/scanOrderCreate', json=payload)

    def place_clothes(self, order_no: str) -> Dict[str, Any]:
        return self._request('POST', '/device/placeClothes', json={'orderNo': order_no})

    def create_underway(self, order_no: str) -> Dict[str, Any]:
        payload = {
            'autoSelectPromotion': False,
            'promotionList': [],
            'orderNo': order_no,
        }
        return self._request('POST', '/trade/underway/create', json=payload)

    def prepay(self, order_no: str) -> Dict[str, Any]:
        return self._request('POST', '/pay/prePay', json={'payMethod': 1001, 'orderNo': order_no})

    def pay(self, prepay_param: str) -> Dict[str, Any]:
        result = self._request('POST', '/pay/pay', json={'prepayParam': prepay_param})
        if not result.get('ok'):
            return result
        data = result.get('data') or {}
        if data.get('success') is not True:
            return {
                'ok': False,
                'error_type': 'business',
                'msg': '支付接口返回成功，但设备未确认启动',
                'code': result.get('code'),
                'data': data,
                'raw': result.get('raw'),
            }
        return result

    def get_orders(self) -> Dict[str, Any]:
        return self._request('POST', '/trade/underway/orderList', json={})

    def finish_order(self, order_no: str) -> Dict[str, Any]:
        result = self._request('POST', '/trade/finishByOrder', json={'orderNo': order_no})
        if not result.get('ok'):
            return result
        if result.get('data') is not True:
            return {
                'ok': False,
                'error_type': 'business',
                'msg': '云端未确认订单已结束',
                'code': result.get('code'),
                'data': result.get('data'),
                'raw': result.get('raw'),
            }
        return result
