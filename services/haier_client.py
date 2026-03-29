from __future__ import annotations

import json
import time

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
    ORDER_DETAIL_SYNC_DELAY_MS,
    SSL_VERIFY,
)


class HaierClient:
    WASHER_CATEGORY_CODE = '00'
    DRYER_CATEGORY_CODE = '02'
    MODE_VARIANT_SCALE = 1000

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

    def near_positions(self, lng: float, lat: float, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        return self._request(
            'POST',
            '/position/nearPosition',
            json={'lng': lng, 'lat': lat, 'page': page, 'pageSize': page_size},
        )

    def use_position_list(self, lng: float, lat: float, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        return self._request(
            'POST',
            '/position/usePositionList',
            json={'lng': lng, 'lat': lat, 'page': page, 'pageSize': page_size},
        )

    def position_detail(self, position_id: str) -> Dict[str, Any]:
        return self._request('GET', '/position/positionDetail', params={'id': position_id})

    def floor_code_list(self, position_id: str) -> Dict[str, Any]:
        return self._request('GET', '/position/floorCodeList', params={'positionId': position_id})

    def position_device(self, position_id: str) -> Dict[str, Any]:
        return self._request('GET', '/position/positionDevice', params={'id': position_id})

    def device_detail_page(self, position_id: str, category_code: str = '00', page: int = 1, page_size: int = 50, floor_code: str = '') -> Dict[str, Any]:
        return self._request(
            'POST',
            '/position/deviceDetailPage',
            json={
                'positionId': str(position_id),
                'categoryCode': category_code,
                'page': page,
                'floorCode': floor_code,
                'pageSize': page_size,
            },
        )

    def goods_last_run_info(self, goods_id: int | str, category_code: str = '00') -> Dict[str, Any]:
        return self._request('POST', '/goods/last/runInfo', json={'goodsId': int(goods_id), 'categoryCode': category_code})

    def goods_verify(self, goods_id: int | str, category_code: str = '00') -> Dict[str, Any]:
        result = self._request('POST', '/goods/verify', json={'goodsId': int(goods_id), 'categoryCode': category_code})
        if not result.get('ok'):
            return result

        data = result.get('data') or {}
        if data.get('isSuccess') is not True:
            return {
                'ok': False,
                'error_type': 'business',
                'msg': data.get('msg') or result.get('msg') or '验单未通过，暂时无法继续支付。',
                'code': result.get('code'),
                'data': data,
                'raw': result.get('raw'),
            }
        return result

    def verify_goods_detail(self, goods_detail: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(goods_detail, dict) or not goods_detail:
            return {
                'ok': False,
                'error_type': 'invalid_response',
                'msg': '读取设备详情成功，但返回数据为空。',
                'raw': goods_detail,
            }

        goods_id = str(goods_detail.get('id') or goods_detail.get('goodsId') or '').strip()
        category_code = self.extract_category_code(goods_detail, default='')
        if not goods_id or not category_code:
            return {
                'ok': False,
                'error_type': 'invalid_response',
                'msg': '无法解析当前设备的 goodsId 或类型。',
                'raw': goods_detail,
            }

        return self.goods_verify(goods_id, category_code=category_code)

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        if value in (None, ''):
            return None
        try:
            normalized = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    @classmethod
    def encode_mode_selection(cls, goods_item_id: Any, duration: Any) -> int:
        goods_item = cls._coerce_positive_int(goods_item_id)
        duration_value = cls._coerce_positive_int(duration)
        if goods_item is None or duration_value is None:
            raise ValueError('goods_item_id and duration must be positive integers')
        return -((goods_item * cls.MODE_VARIANT_SCALE) + duration_value)

    @classmethod
    def decode_mode_selection(cls, mode_id: Any) -> tuple[int, int | None]:
        try:
            numeric_mode_id = int(mode_id)
        except (TypeError, ValueError) as exc:
            raise ValueError('模式编号无效') from exc

        if numeric_mode_id >= 0:
            return numeric_mode_id, None

        encoded = abs(numeric_mode_id)
        goods_item_id = encoded // cls.MODE_VARIANT_SCALE
        duration = encoded % cls.MODE_VARIANT_SCALE
        if goods_item_id <= 0 or duration <= 0:
            raise ValueError('模式编号无效')
        return goods_item_id, duration

    @classmethod
    def extract_mode_durations(cls, mode_item: Dict[str, Any]) -> list[int]:
        if not isinstance(mode_item, dict):
            return []

        durations: set[int] = set()
        ext_items = ((mode_item.get('extAttrDto') or {}).get('items') or [])
        for item in ext_items:
            if not isinstance(item, dict):
                continue
            duration = cls._coerce_positive_int(item.get('unitAmount') or item.get('unit'))
            if duration is not None:
                durations.add(duration)

        fallback_duration = cls._coerce_positive_int(mode_item.get('unitAmount') or mode_item.get('unit'))
        if fallback_duration is not None:
            durations.add(fallback_duration)

        return sorted(durations)

    @classmethod
    def extract_category_code(cls, payload: Dict[str, Any] | None, default: str = WASHER_CATEGORY_CODE) -> str:
        if not isinstance(payload, dict):
            return default

        candidates = [
            payload.get('categoryCode'),
            payload.get('deviceCategory'),
        ]

        order_item = (payload.get('orderItemList') or [{}])[0]
        if isinstance(order_item, dict):
            candidates.extend(
                [
                    order_item.get('categoryCode'),
                    ((order_item.get('goodsItemInfoDto') or {}).get('categoryCode')),
                ]
            )
            goods_item_info = order_item.get('goodsItemInfo')
            if isinstance(goods_item_info, str):
                try:
                    parsed_goods_item_info = json.loads(goods_item_info)
                except ValueError:
                    parsed_goods_item_info = {}
                if isinstance(parsed_goods_item_info, dict):
                    candidates.append(parsed_goods_item_info.get('categoryCode'))

        device_info_list = ((payload.get('uniqueInfo') or {}).get('deviceInfoList') or [])
        if device_info_list and isinstance(device_info_list[0], dict):
            candidates.append(device_info_list[0].get('deviceCategory'))

        for candidate in candidates:
            text = str(candidate or '').strip()
            if text:
                return text
        return default

    @classmethod
    def build_scan_order_payload(cls, goods_detail: Dict[str, Any], mode_id: int, hash_key: str) -> Dict[str, Any]:
        if not isinstance(goods_detail, dict) or not goods_detail:
            raise ValueError('设备详情为空，无法创建扫码订单。')

        goods_id = goods_detail.get('id') or goods_detail.get('goodsId')
        goods_id_text = str(goods_id or '').strip()
        if not goods_id_text:
            raise ValueError('设备详情缺少 goodsId，无法创建扫码订单。')

        resolved_mode_id, requested_duration = cls.decode_mode_selection(mode_id)
        mode_item = next(
            (
                item
                for item in (goods_detail.get('items') or [])
                if isinstance(item, dict) and cls._coerce_positive_int(item.get('id')) == resolved_mode_id
            ),
            None,
        )
        if not mode_item:
            raise ValueError('在设备详情中未找到所选模式。')

        category_code = cls.extract_category_code(goods_detail)
        if category_code == cls.DRYER_CATEGORY_CODE:
            durations = cls.extract_mode_durations(mode_item)
            if requested_duration is not None:
                if requested_duration not in durations:
                    raise ValueError(f'烘干模式不支持 {requested_duration} 分钟。')
                duration = requested_duration
            else:
                duration = max(durations) if durations else None
            if duration is None:
                raise ValueError('烘干模式缺少可解析时长，无法创建订单。')

            purchase_item = {
                'goodsId': goods_id_text,
                'goodsItemId': resolved_mode_id,
                'soldType': 2,
                'amount': duration,
                'unit': duration,
                'num': duration,
            }
            optional_info: Dict[str, Any] = {'deviceInfo': {}}
        else:
            purchase_item = {
                'goodsId': goods_id_text,
                'goodsItemId': resolved_mode_id,
                'soldType': 1,
                'amount': 1,
                'num': 1,
            }
            optional_info = {}

        return {
            'optionalInfo': optional_info,
            'purchaseList': [purchase_item],
            'hashKey': hash_key,
        }

    def create_scan_order(
        self,
        goods_id: str,
        mode_id: int,
        hash_key: str,
        *,
        goods_detail: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        detail = goods_detail
        if detail is None:
            detail_res = self.goods_details(goods_id)
            if not detail_res.get('ok'):
                return detail_res
            detail = detail_res.get('data') or {}

        try:
            payload = self.build_scan_order_payload(detail or {}, mode_id, hash_key)
        except ValueError as exc:
            return {
                'ok': False,
                'error_type': 'invalid_mode',
                'msg': str(exc),
                'raw': detail,
            }
        return self._request('POST', '/trade/scanOrderCreate', json=payload)

    def create_order(
        self,
        goods_id: str,
        mode_id: int,
        hash_key: str,
        *,
        goods_detail: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self.create_scan_order(goods_id=goods_id, mode_id=mode_id, hash_key=hash_key, goods_detail=goods_detail)

    def create_lock_order(self, goods_id: str, mode_id: int, hash_key: str = '', reserve_method: Any = None) -> Dict[str, Any]:
        payload = {
            'optionalInfo': {},
            'purchaseList': [
                {
                    'goodsId': int(goods_id),
                    'goodsItemId': int(mode_id),
                    'soldType': 1,
                    'amount': 1,
                    'num': 1,
                }
            ],
            'hashKey': hash_key,
            'reserveMethod': reserve_method,
        }
        return self._request('POST', '/trade/lockOrderCreate', json=payload)

    def order_detail(self, order_no: str) -> Dict[str, Any]:
        result = self._request('GET', '/trade/order/detail', params={'orderNo': order_no})
        if not result.get('ok') or ORDER_DETAIL_SYNC_DELAY_MS <= 0:
            return result

        time.sleep(ORDER_DETAIL_SYNC_DELAY_MS / 1000)
        synced_result = self._request('GET', '/trade/order/detail', params={'orderNo': order_no})
        if not synced_result.get('ok'):
            return result
        return synced_result

    def place_clothes(self, order_no: str) -> Dict[str, Any]:
        return self._request('POST', '/device/placeClothes', json={'orderNo': order_no})

    def checkstand(self, order_no: str) -> Dict[str, Any]:
        return self._request('POST', '/pay/checkstand', json={'orderNo': order_no})

    def underway_preview(self, order_no: str, auto_select_promotion: bool = True) -> Dict[str, Any]:
        return self._request(
            'POST',
            '/trade/underway/preview/V2',
            json={'autoSelectPromotion': auto_select_promotion, 'orderNo': order_no, 'promotionList': []},
        )

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

    def get_underway_orders(self) -> Dict[str, Any]:
        return self._request('POST', '/trade/underway/orderList', json={})

    def get_orders(self) -> Dict[str, Any]:
        return self.get_underway_orders()

    def list_history_orders(self, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        return self._request('POST', '/trade/list', json={'page': page, 'pageSize': page_size})

    def cancel_order(self, order_no: str) -> Dict[str, Any]:
        result = self._request('POST', '/trade/cancel', json={'orderNo': order_no})
        if not result.get('ok'):
            return result
        if result.get('data') is not True:
            return {
                'ok': False,
                'error_type': 'business',
                'msg': '云端未确认订单已取消',
                'code': result.get('code'),
                'data': result.get('data'),
                'raw': result.get('raw'),
            }
        return result

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
