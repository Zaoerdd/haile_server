import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if 'urllib3' not in sys.modules:
    sys.modules['urllib3'] = types.SimpleNamespace(
        disable_warnings=lambda *args, **kwargs: None,
        exceptions=types.SimpleNamespace(InsecureRequestWarning=Warning),
    )

if 'requests' not in sys.modules:
    class _DummyRequestException(Exception):
        pass

    class _DummyTimeout(_DummyRequestException):
        pass

    class _DummySession:
        def request(self, *args, **kwargs):
            raise AssertionError('network access should not occur in machine precheck tests')

    sys.modules['requests'] = types.SimpleNamespace(
        Session=_DummySession,
        Timeout=_DummyTimeout,
        RequestException=_DummyRequestException,
    )

from services.reservation_service import reservation_service
from services.workflow import ProcessState, WorkflowManager


RUNNING_MESSAGE = '抱歉，设备正在运行中，请更换设备重新下单。'


class WorkflowCreateOrderPrecheckTests(unittest.TestCase):
    @patch('services.workflow.database.init')
    def test_step_create_order_returns_verify_message_before_create(self, _database_init_mock):
        manager = WorkflowManager()
        state = ProcessState(process_id='process-1', qr_code='qr-1', mode_id=1009754293)
        state.context['goods_id'] = '10661674'
        state.context['hash_key'] = 'hash-key'

        client = MagicMock()
        client.goods_details.return_value = {
            'ok': True,
            'data': {'id': 10661674, 'categoryCode': '00'},
            'raw': {'detail': True},
        }
        client.verify_goods_detail.return_value = {
            'ok': False,
            'error_type': 'business',
            'msg': RUNNING_MESSAGE,
            'code': 0,
            'data': {'isSuccess': False, 'msg': RUNNING_MESSAGE},
            'raw': {'code': 0, 'data': {'isSuccess': False, 'msg': RUNNING_MESSAGE}},
        }

        result = manager._step_create_order(state, client)

        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['msg'], RUNNING_MESSAGE)
        self.assertEqual(result['errorType'], 'business')
        client.create_order.assert_not_called()


class ReservationCreateOrderPrecheckTests(unittest.TestCase):
    @patch.object(reservation_service, '_find_existing_pending_order', return_value=(None, None, None))
    @patch('services.reservation_service.HaierClient')
    def test_create_pending_order_returns_verify_message_before_create(self, haier_client_mock, _find_existing_pending_order_mock):
        client = MagicMock()
        haier_client_mock.return_value = client
        client.scan_goods.return_value = {
            'ok': True,
            'data': {'goodsId': 10661674, 'activityHashKey': 'hash-key'},
            'raw': {'scan': True},
        }
        client.goods_details.return_value = {
            'ok': True,
            'data': {'id': 10661674, 'categoryCode': '00'},
            'raw': {'detail': True},
        }
        client.verify_goods_detail.return_value = {
            'ok': False,
            'error_type': 'business',
            'msg': RUNNING_MESSAGE,
            'code': 0,
            'data': {'isSuccess': False, 'msg': RUNNING_MESSAGE},
            'raw': {'code': 0, 'data': {'isSuccess': False, 'msg': RUNNING_MESSAGE}},
        }

        task = types.SimpleNamespace(qr_code='qr-1', mode_id=1009754293)
        ok, message, debug, status = reservation_service._create_pending_order(task, token='token')

        self.assertFalse(ok)
        self.assertEqual(message, RUNNING_MESSAGE)
        self.assertEqual(status, 'failed_business')
        self.assertIn('goodsVerify', debug)
        client.create_scan_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
