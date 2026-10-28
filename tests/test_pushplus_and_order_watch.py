import unittest
from unittest.mock import patch

from services import notifications
from services.notifications import PushPlusNotifier, is_http_url
from services.order_watch import (
    build_message,
    extract_finish_time,
    is_finished_state,
    is_running_state,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=''):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


class FakeRequests:
    """替代 notifications 里的 requests 模块，记录调用并可注入异常。"""

    class RequestException(Exception):
        pass

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'timeout': timeout})
        if self.error is not None:
            raise self.error
        return self.response


class PushPlusNotifierTests(unittest.TestCase):
    def test_is_http_url(self):
        self.assertTrue(is_http_url('http://example.com/send'))
        self.assertTrue(is_http_url('https://pushplus.plus/send'))
        self.assertFalse(is_http_url('d734aa7155d1455eb9a276352b6878db'))
        self.assertFalse(is_http_url(''))

    def test_missing_target_skips_request(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse({'code': 200}))
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify('', '标题', '内容')
        self.assertFalse(result['sent'])
        self.assertEqual(result['reason'], 'missing_pushplus_target')
        self.assertEqual(fake.calls, [])

    def test_token_posts_to_official_endpoint(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse({'code': 200, 'msg': '执行成功'}))
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify('abc123', '标题', '<p>内容</p>')
        self.assertTrue(result['sent'])
        call = fake.calls[0]
        self.assertIn('pushplus', call['url'])
        self.assertEqual(call['json']['token'], 'abc123')
        self.assertEqual(call['json']['title'], '标题')
        self.assertEqual(call['json']['template'], 'html')

    def test_url_target_posts_directly(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse({'code': 200}))
        target = 'https://gateway.example.com/push?key=1'
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify(target, '标题', '内容')
        self.assertTrue(result['sent'])
        self.assertEqual(fake.calls[0]['url'], target)
        self.assertNotIn('token', fake.calls[0]['json'])

    def test_rate_limit_code_enters_cooldown(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse({'code': 999, 'msg': '服务端验证错误'}))
        with patch.object(notifications, 'requests', fake):
            first = notifier.notify('abc123', '标题', '内容')
            self.assertFalse(first['sent'])
            self.assertEqual(first['reason'], 'rate_limited')
            self.assertEqual(len(fake.calls), 1)

            second = notifier.notify('abc123', '标题', '内容')
            self.assertFalse(second['sent'])
            self.assertEqual(second['reason'], 'rate_limited')
            self.assertEqual(len(fake.calls), 1, '冷却期内不应再发请求')

    def test_request_exception_is_reported(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(error=FakeRequests.RequestException('boom'))
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify('abc123', '标题', '内容')
        self.assertFalse(result['sent'])
        self.assertEqual(result['reason'], 'request_failed')

    def test_non_json_response_is_reported(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse(None, status_code=502, text='<html>502</html>'))
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify('abc123', '标题', '内容')
        self.assertFalse(result['sent'])
        self.assertEqual(result['reason'], 'pushplus_rejected')

    def test_token_with_pasted_whitespace_is_cleaned(self):
        notifier = PushPlusNotifier()
        fake = FakeRequests(FakeResponse({'code': 200}))
        token = 'd734aa7155d1455eb9a276352b6878db'
        with patch.object(notifications, 'requests', fake):
            result = notifier.notify(f'  {token[:16]}\n{token[16:]}  ', '标题', '内容')
        self.assertTrue(result['sent'])
        self.assertEqual(fake.calls[0]['json']['token'], token)

    def test_normalize_target(self):
        token = 'd734aa7155d1455eb9a276352b6878db'
        self.assertEqual(notifications.normalize_target(token), token)
        self.assertEqual(notifications.normalize_target(f' {token} \r\n'), token)
        self.assertEqual(notifications.normalize_target(f'{token[:10]}\t{token[10:]}'), token)
        self.assertEqual(
            notifications.normalize_target('  https://x.example/send?a=1  '),
            'https://x.example/send?a=1',
            'URL 只做首尾 trim，不破坏查询串',
        )
        self.assertEqual(notifications.normalize_target(None), '')

    def test_describe_target_masks_token(self):
        token = 'd734aa7155d1455eb9a276352b6878db'
        self.assertEqual(notifications.describe_target(token), 'd734****78db')
        self.assertEqual(notifications.describe_target('short'), 'short')
        self.assertEqual(notifications.describe_target(''), '')
        self.assertEqual(
            notifications.describe_target('https://x.example/send'),
            'https://x.example/send',
        )


class OrderWatchStateTests(unittest.TestCase):
    def test_is_running_state(self):
        self.assertTrue(is_running_state('运行中'))
        self.assertTrue(is_running_state('洗涤中'))
        self.assertTrue(is_running_state('预计完成 15:30'))
        self.assertFalse(is_running_state('待付款'))
        self.assertFalse(is_running_state('待验证'))
        self.assertFalse(is_running_state('已完成'))
        self.assertFalse(is_running_state(''))

    def test_is_finished_state(self):
        self.assertTrue(is_finished_state('已完成'))
        self.assertTrue(is_finished_state('已结束'))
        self.assertTrue(is_finished_state('已取消'))
        self.assertFalse(is_finished_state('运行中'))

    def test_extract_finish_time_prefers_fulfilling_item(self):
        detail = {
            'fulfillInfo': {'fulfillingItem': {'finishTime': '2026-10-22 15:30:00'}},
            'orderItemList': [{'finishTime': '2026-10-22 16:00:00'}],
        }
        parsed = extract_finish_time(detail)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.strftime('%H:%M'), '15:30')

    def test_extract_finish_time_falls_back_to_order_item(self):
        detail = {'orderItemList': [{'finishTime': '2026-10-22 16:05:00'}]}
        parsed = extract_finish_time(detail)
        self.assertEqual(parsed.strftime('%H:%M'), '16:05')

    def test_extract_finish_time_handles_missing(self):
        self.assertIsNone(extract_finish_time({}))
        self.assertIsNone(extract_finish_time(None))

    def test_build_message_skips_empty_lines(self):
        html = build_message('洗衣已开始', ['机器：A', '', '订单：1'])
        self.assertIn('<h3>洗衣已开始</h3>', html)
        self.assertIn('<p>机器：A</p>', html)
        self.assertNotIn('<p></p>', html)


if __name__ == '__main__':
    unittest.main()
