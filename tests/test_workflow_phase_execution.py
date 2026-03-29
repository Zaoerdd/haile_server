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
            raise AssertionError('network access should not occur in workflow phase tests')

    sys.modules['requests'] = types.SimpleNamespace(
        Session=_DummySession,
        Timeout=_DummyTimeout,
        RequestException=_DummyRequestException,
    )

from services.workflow import ProcessState, WorkflowManager


def build_pending_detail(order_no: str) -> dict:
    return {
        'orderNo': order_no,
        'state': 50,
        'stateDesc': 'pending',
        'pageCode': 'waiting_choose_ump',
        'buttonSwitch': {
            'canCancel': True,
            'canCloseOrder': False,
            'canPay': True,
        },
        'orderItemList': [
            {
                'goodsName': 'machine-1',
                'goodsItemName': 'mode-1',
                'goodsId': 'goods-1',
            }
        ],
    }


class WorkflowPhaseExecutionTests(unittest.TestCase):
    @patch('services.workflow.HaierClient')
    @patch('services.workflow.database.init')
    def test_execute_next_runs_phase_one_to_payment_stage(self, _database_init_mock, haier_client_mock):
        manager = WorkflowManager()
        state = ProcessState(process_id='process-1', qr_code='qr-1', mode_id=1009754293)
        client = MagicMock()
        haier_client_mock.return_value = client

        step_calls = []

        def scan_side_effect(current_state, _client):
            step_calls.append(1)
            current_state.context['goods_id'] = 'goods-1'
            current_state.context['hash_key'] = 'hash-1'
            current_state.current_step = 2
            return {'status': 'success', 'msg': 'scan ok', 'process': current_state.to_dict()}

        def create_side_effect(current_state, _client):
            step_calls.append(2)
            current_state.context['order_no'] = 'order-1'
            current_state.current_step = 3
            return {'status': 'success', 'msg': 'create ok', 'process': current_state.to_dict()}

        def place_side_effect(current_state, _client):
            step_calls.append(3)
            current_state.current_step = 4
            return {'status': 'success', 'msg': 'place ok', 'process': current_state.to_dict()}

        with patch.object(manager, 'get', return_value=state), \
            patch.object(manager, '_save_state'), \
            patch.object(manager, '_sync_remote_order_state', return_value=None), \
            patch.object(manager, '_step_scan', side_effect=scan_side_effect), \
            patch.object(manager, '_step_create_order', side_effect=create_side_effect), \
            patch.object(manager, '_step_place_clothes', side_effect=place_side_effect):
            result = manager.execute_next('process-1', 'token')

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['process']['currentStep'], 4)
        self.assertEqual(result['process']['contextSummary']['orderNo'], 'order-1')
        self.assertEqual(step_calls, [1, 2, 3])

    @patch('services.workflow.HaierClient')
    @patch('services.workflow.database.init')
    def test_execute_next_rolls_back_phase_one_after_create_failure(self, _database_init_mock, haier_client_mock):
        manager = WorkflowManager()
        state = ProcessState(process_id='process-2', qr_code='qr-2', mode_id=1009754293)
        client = MagicMock()
        haier_client_mock.return_value = client

        def scan_side_effect(current_state, _client):
            current_state.context['goods_id'] = 'goods-1'
            current_state.context['hash_key'] = 'hash-1'
            current_state.current_step = 2
            return {'status': 'success', 'msg': 'scan ok', 'process': current_state.to_dict()}

        def create_side_effect(current_state, _client):
            current_state.context['order_no'] = 'order-2'
            return {
                'status': 'error',
                'errorType': 'business',
                'msg': 'create failed',
                'debug': {'reason': 'boom'},
                'process': current_state.to_dict(),
            }

        with patch.object(manager, 'get', return_value=state), \
            patch.object(manager, '_save_state'), \
            patch.object(manager, '_sync_remote_order_state', return_value=None), \
            patch.object(manager, '_step_scan', side_effect=scan_side_effect), \
            patch.object(manager, '_step_create_order', side_effect=create_side_effect), \
            patch.object(manager, 'cleanup_order_by_no', return_value={'status': 'success', 'msg': 'cleaned'} ) as cleanup_mock:
            result = manager.execute_next('process-2', 'token')

        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['msg'], 'create failed')
        self.assertEqual(result['process']['currentStep'], 1)
        self.assertIsNone(result['process']['contextSummary']['orderNo'])
        cleanup_mock.assert_called_once_with(token='token', order_no='order-2')

    @patch('services.workflow.HaierClient')
    @patch('services.workflow.database.init')
    def test_execute_next_rolls_back_phase_two_after_pay_failure(self, _database_init_mock, haier_client_mock):
        manager = WorkflowManager()
        state = ProcessState(process_id='process-3', qr_code='qr-3', mode_id=1009754293, current_step=4)
        state.context.update({'goods_id': 'goods-1', 'hash_key': 'hash-1', 'order_no': 'order-3'})

        client = MagicMock()
        client.order_detail.return_value = {
            'ok': True,
            'data': build_pending_detail('order-3'),
            'raw': {'detail': True},
        }
        haier_client_mock.return_value = client

        def prepare_side_effect(current_state, _client):
            current_state.context['prepay_param'] = 'prepay-1'
            current_state.current_step = 5
            return {'status': 'success', 'msg': 'prepare ok', 'process': current_state.to_dict()}

        def pay_side_effect(current_state, _client):
            return {
                'status': 'error',
                'errorType': 'business',
                'msg': 'pay failed',
                'debug': {'reason': 'retry later'},
                'process': current_state.to_dict(),
            }

        with patch.object(manager, 'get', return_value=state), \
            patch.object(manager, '_save_state'), \
            patch.object(manager, '_sync_remote_order_state', return_value=None), \
            patch.object(manager, '_step_prepare_payment', side_effect=prepare_side_effect), \
            patch.object(manager, '_step_pay', side_effect=pay_side_effect):
            result = manager.execute_next('process-3', 'token')

        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['msg'], 'pay failed')
        self.assertEqual(result['process']['currentStep'], 4)
        self.assertEqual(result['process']['contextSummary']['orderNo'], 'order-3')
        self.assertFalse(result['process']['contextSummary']['prepayReady'])


if __name__ == '__main__':
    unittest.main()
