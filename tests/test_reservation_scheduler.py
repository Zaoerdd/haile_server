import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, MagicMock, patch

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
            raise AssertionError('network access should not occur in reservation tests')

    sys.modules['requests'] = types.SimpleNamespace(
        Session=_DummySession,
        Timeout=_DummyTimeout,
        RequestException=_DummyRequestException,
    )

import services.reservation_service as reservation_module
from services.reservation_service import reservation_service


def build_order_detail(
    *,
    order_no: str,
    state: int = 50,
    state_desc: str = 'pending',
    page_code: str = 'waiting_choose_ump',
    invalid_time: datetime | str | None = None,
    can_pay: bool = True,
    can_cancel: bool = True,
    can_close: bool = False,
) -> dict:
    detail = {
        'orderNo': order_no,
        'state': state,
        'stateDesc': state_desc,
        'pageCode': page_code,
        'realPrice': '3.50',
        'createTime': '2026-03-27T10:00:00+00:00',
        'payTime': None,
        'completeTime': None,
        'finishTime': None,
        'buttonSwitch': {
            'canCancel': can_cancel,
            'canCloseOrder': can_close,
            'canPay': can_pay,
        },
        'orderItemList': [
            {
                'goodsName': 'machine-1',
                'goodsItemName': 'mode-1',
                'shopName': 'shop-1',
                'goodsId': 'goods-1',
            }
        ],
    }
    if isinstance(invalid_time, str):
        detail['invalidTime'] = invalid_time
    elif invalid_time is not None:
        detail['invalidTime'] = invalid_time.isoformat()
    return detail


def build_order_snapshot(order_detail: dict) -> dict:
    return reservation_service._normalize_current_order(order_detail)


def build_task_row(
    *,
    status: str,
    target_time: datetime,
    start_at: datetime,
    hold_until: datetime,
    current_order: dict | None = None,
    active_order_no: str | None = None,
) -> dict:
    timestamp = (target_time - timedelta(minutes=5)).isoformat()
    return {
        'id': 1,
        'title': 'test-task',
        'machine_source': 'scan',
        'machine_id': 'machine-1',
        'machine_name': 'machine-1',
        'room_id': None,
        'room_name': None,
        'qr_code': 'qr-1',
        'mode_id': 1,
        'mode_name': 'mode-1',
        'schedule_type': 'once',
        'target_time': target_time.isoformat(),
        'weekday': None,
        'time_of_day': None,
        'timezone_name': None,
        'lead_minutes': 60,
        'status': status,
        'active_order_no': active_order_no,
        'start_at': start_at.isoformat(),
        'hold_until': hold_until.isoformat(),
        'last_checked_at': None,
        'last_error': None,
        'current_order_snapshot': json.dumps(current_order, ensure_ascii=False) if current_order is not None else None,
        'last_run_at': None,
        'created_at': timestamp,
        'updated_at': timestamp,
    }


class ReservationSchedulerDelayTests(unittest.TestCase):
    @patch('services.reservation_service.database.fetch_all')
    @patch('services.reservation_service.now_local')
    def test_uses_early_renew_time_as_next_wakeup(self, now_local_mock, fetch_all_mock):
        now = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)
        invalid_at = now + timedelta(seconds=67)
        now_local_mock.return_value = now
        fetch_all_mock.return_value = [
            build_task_row(
                status='holding',
                target_time=now + timedelta(minutes=10),
                start_at=now - timedelta(minutes=1),
                hold_until=now + timedelta(minutes=11),
                current_order=build_order_snapshot(build_order_detail(order_no='old-order', invalid_time=invalid_at)),
                active_order_no='old-order',
            )
        ]

        delay = reservation_service.next_poll_delay_seconds(30)

        self.assertAlmostEqual(delay, 7.0, delta=0.2)

    @patch('services.reservation_service.database.fetch_all')
    @patch('services.reservation_service.now_local')
    def test_uses_future_start_time_before_default_interval(self, now_local_mock, fetch_all_mock):
        now = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)
        now_local_mock.return_value = now
        fetch_all_mock.return_value = [
            build_task_row(
                status='scheduled',
                target_time=now + timedelta(minutes=10),
                start_at=now + timedelta(seconds=4),
                hold_until=now + timedelta(minutes=20),
            )
        ]

        delay = reservation_service.next_poll_delay_seconds(30)

        self.assertAlmostEqual(delay, 4.0, delta=0.2)

    @patch('services.reservation_service.database.fetch_all')
    @patch('services.reservation_service.now_local')
    def test_due_task_without_future_deadline_keeps_default_interval(self, now_local_mock, fetch_all_mock):
        now = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)
        now_local_mock.return_value = now
        fetch_all_mock.return_value = [
            build_task_row(
                status='scheduled',
                target_time=now + timedelta(minutes=1),
                start_at=now - timedelta(seconds=2),
                hold_until=now - timedelta(seconds=1),
            )
        ]

        delay = reservation_service.next_poll_delay_seconds(30)

        self.assertEqual(delay, 30)


class ReservationTimezoneDefaultsTests(unittest.TestCase):
    def test_now_local_uses_fixed_shanghai_timezone(self):
        current = reservation_module.now_local()

        self.assertEqual(current.utcoffset(), reservation_module.REMOTE_APP_TIMEZONE.utcoffset(current))

    def test_parse_iso_treats_naive_values_as_shanghai_time(self):
        parsed = reservation_module.parse_iso('2026-03-30T08:00:00')

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset(), reservation_module.REMOTE_APP_TIMEZONE.utcoffset(parsed))
        self.assertEqual(parsed.hour, 8)
        self.assertEqual(parsed.minute, 0)

    def test_parse_iso_converts_aware_values_into_shanghai_timezone(self):
        parsed = reservation_module.parse_iso('2026-03-30T00:00:00+00:00')

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset(), reservation_module.REMOTE_APP_TIMEZONE.utcoffset(parsed))
        self.assertEqual(parsed.hour, 8)
        self.assertEqual(parsed.minute, 0)

    def test_resolve_timezone_defaults_to_fixed_shanghai_timezone(self):
        resolved = reservation_module.resolve_timezone(None)

        self.assertEqual(resolved.utcoffset(None), reservation_module.REMOTE_APP_TIMEZONE.utcoffset(None))


class ReservationEarlyRenewTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)
        self.target_time = self.now + timedelta(minutes=5)
        self.start_at = self.now - timedelta(minutes=1)
        self.hold_until = self.now + timedelta(minutes=10)
        self.old_invalid_at = self.now + timedelta(seconds=30)
        self.old_pending_detail = build_order_detail(order_no='old-order', invalid_time=self.old_invalid_at)
        self.old_closed_detail = build_order_detail(
            order_no='old-order',
            state=401,
            state_desc='closed',
            page_code='',
            invalid_time=self.old_invalid_at,
            can_pay=False,
            can_cancel=False,
            can_close=False,
        )
        self.new_pending_detail = build_order_detail(
            order_no='new-order',
            invalid_time=self.now + timedelta(minutes=4),
        )
        self.task_row = build_task_row(
            status='holding',
            target_time=self.target_time,
            start_at=self.start_at,
            hold_until=self.hold_until,
            current_order=build_order_snapshot(self.old_pending_detail),
            active_order_no='old-order',
        )

    def _process_due_tasks(
        self,
        *,
        retry_side_effect,
        cancel_result,
        create_result=None,
    ):
        client = MagicMock()
        client.cancel_order.return_value = cancel_result

        with patch('services.reservation_service.now_local', return_value=self.now), \
            patch('services.reservation_service.database.fetch_all', return_value=[self.task_row]), \
            patch('services.reservation_service.settings_store.get_effective_settings', return_value=types.SimpleNamespace(token='token')), \
            patch('services.reservation_service.HaierClient', return_value=client), \
            patch.object(reservation_service, '_retry_order_detail', side_effect=retry_side_effect) as retry_mock, \
            patch.object(reservation_service, '_create_pending_order', return_value=create_result) as create_mock, \
            patch.object(reservation_service, '_update_task') as update_task_mock, \
            patch.object(reservation_service, '_record_event') as record_event_mock, \
            patch.object(reservation_service, '_notify') as notify_mock, \
            patch.object(reservation_service, '_ensure_process_for_task', return_value=None), \
            patch.object(reservation_service, '_sync_workflow_process') as sync_process_mock:
            result = reservation_service.process_due_tasks()

        return {
            'result': result,
            'client': client,
            'retry': retry_mock,
            'create': create_mock,
            'update_task': update_task_mock,
            'record_event': record_event_mock,
            'notify': notify_mock,
            'sync_process': sync_process_mock,
        }

    def test_early_renew_recreates_before_invalid_time(self):
        outcome = self._process_due_tasks(
            retry_side_effect=[self.old_pending_detail, self.old_closed_detail],
            cancel_result={'ok': True, 'raw': {'ok': True}},
            create_result=(True, 'new-order', self.new_pending_detail, 'created'),
        )

        self.assertEqual(outcome['result']['recreated'], 1)
        outcome['client'].cancel_order.assert_called_once_with('old-order')
        self.assertEqual(outcome['create'].call_args.kwargs['excluded_order_nos'], {'old-order'})
        outcome['update_task'].assert_any_call(
            1,
            status='holding',
            active_order_no='new-order',
            current_order_snapshot=ANY,
            last_checked_at=ANY,
            last_run_at=ANY,
            last_error=None,
        )
        event_types = [call.args[1] for call in outcome['record_event'].call_args_list]
        self.assertIn('order_early_recreated', event_types)

    def test_early_renew_create_failure_returns_task_to_scheduled(self):
        outcome = self._process_due_tasks(
            retry_side_effect=[self.old_pending_detail, self.old_closed_detail],
            cancel_result={'ok': True, 'raw': {'ok': True}},
            create_result=(False, 'machine occupied', {'reason': 'busy'}, 'failed_business'),
        )

        self.assertEqual(outcome['result']['recreated'], 0)
        outcome['update_task'].assert_any_call(
            1,
            status='scheduled',
            active_order_no=None,
            current_order_snapshot=None,
            last_checked_at=ANY,
            last_error='machine occupied',
        )
        event_types = [call.args[1] for call in outcome['record_event'].call_args_list]
        self.assertIn('order_early_recreate_failed', event_types)

    def test_cancel_failure_still_creates_when_old_order_already_closed(self):
        outcome = self._process_due_tasks(
            retry_side_effect=[self.old_pending_detail, self.old_closed_detail],
            cancel_result={'ok': False, 'msg': 'already closed', 'raw': {'msg': 'already closed'}},
            create_result=(True, 'new-order', self.new_pending_detail, 'created'),
        )

        self.assertEqual(outcome['result']['recreated'], 1)
        outcome['client'].cancel_order.assert_called_once_with('old-order')
        event_types = [call.args[1] for call in outcome['record_event'].call_args_list]
        self.assertIn('order_early_recreated', event_types)

    def test_cancel_failure_keeps_holding_when_old_order_still_pending(self):
        outcome = self._process_due_tasks(
            retry_side_effect=[self.old_pending_detail, self.old_pending_detail],
            cancel_result={'ok': False, 'msg': 'cancel blocked', 'raw': {'msg': 'cancel blocked'}},
            create_result=(True, 'new-order', self.new_pending_detail, 'created'),
        )

        outcome['create'].assert_not_called()
        outcome['update_task'].assert_any_call(
            1,
            current_order_snapshot=ANY,
            last_checked_at=ANY,
            last_error='cancel blocked',
        )
        event_types = [call.args[1] for call in outcome['record_event'].call_args_list]
        self.assertIn('order_early_cancel_failed', event_types)

    def test_missing_invalid_time_keeps_existing_wait_logic(self):
        task_row = build_task_row(
            status='holding',
            target_time=self.target_time,
            start_at=self.start_at,
            hold_until=self.hold_until,
            current_order=build_order_snapshot(build_order_detail(order_no='old-order', invalid_time=None)),
            active_order_no='old-order',
        )
        client = MagicMock()

        with patch('services.reservation_service.now_local', return_value=self.now), \
            patch('services.reservation_service.database.fetch_all', return_value=[task_row]), \
            patch('services.reservation_service.settings_store.get_effective_settings', return_value=types.SimpleNamespace(token='token')), \
            patch('services.reservation_service.HaierClient', return_value=client), \
            patch.object(reservation_service, '_retry_order_detail') as retry_mock, \
            patch.object(reservation_service, '_create_pending_order') as create_mock, \
            patch.object(reservation_service, '_update_task') as update_task_mock, \
            patch.object(reservation_service, '_record_event') as record_event_mock, \
            patch.object(reservation_service, '_notify'), \
            patch.object(reservation_service, '_ensure_process_for_task', return_value=None), \
            patch.object(reservation_service, '_sync_workflow_process'):
            result = reservation_service.process_due_tasks()

        self.assertEqual(result['recreated'], 0)
        client.cancel_order.assert_not_called()
        retry_mock.assert_not_called()
        create_mock.assert_not_called()
        update_task_mock.assert_any_call(1, last_checked_at=ANY, last_error=None)
        event_types = [call.args[1] for call in record_event_mock.call_args_list]
        self.assertNotIn('order_early_recreated', event_types)

    def test_early_renew_uses_shanghai_time_for_naive_invalid_time_under_utc_runtime(self):
        now = datetime(2026, 3, 27, 17, 32, 30, tzinfo=timezone.utc)
        target_time = datetime(2026, 3, 27, 18, 20, 0, tzinfo=timezone.utc)
        start_at = datetime(2026, 3, 27, 17, 20, 0, tzinfo=timezone.utc)
        hold_until = datetime(2026, 3, 27, 18, 30, 0, tzinfo=timezone.utc)
        old_pending_detail = build_order_detail(
            order_no='old-order',
            invalid_time='2026-03-28 01:33:01',
        )
        old_closed_detail = build_order_detail(
            order_no='old-order',
            state=401,
            state_desc='closed',
            page_code='',
            invalid_time='2026-03-28 01:33:01',
            can_pay=False,
            can_cancel=False,
            can_close=False,
        )
        task_row = build_task_row(
            status='holding',
            target_time=target_time,
            start_at=start_at,
            hold_until=hold_until,
            current_order=build_order_snapshot(old_pending_detail),
            active_order_no='old-order',
        )
        client = MagicMock()

        with patch('services.reservation_service.now_local', return_value=now), \
            patch('services.reservation_service.database.fetch_all', return_value=[task_row]), \
            patch('services.reservation_service.settings_store.get_effective_settings', return_value=types.SimpleNamespace(token='token')), \
            patch('services.reservation_service.HaierClient', return_value=client), \
            patch.object(reservation_service, '_retry_order_detail', side_effect=[old_pending_detail, old_closed_detail]) as retry_mock, \
            patch.object(reservation_service, '_create_pending_order', return_value=(True, 'new-order', self.new_pending_detail, 'created')) as create_mock, \
            patch.object(reservation_service, '_update_task') as update_task_mock, \
            patch.object(reservation_service, '_record_event') as record_event_mock, \
            patch.object(reservation_service, '_notify'), \
            patch.object(reservation_service, '_ensure_process_for_task', return_value=None), \
            patch.object(reservation_service, '_sync_workflow_process'):
            result = reservation_service.process_due_tasks()

        self.assertEqual(result['recreated'], 1)
        client.cancel_order.assert_called_once_with('old-order')
        retry_mock.assert_called()
        create_mock.assert_called_once()
        update_task_mock.assert_any_call(
            1,
            status='holding',
            active_order_no='new-order',
            current_order_snapshot=ANY,
            last_checked_at=ANY,
            last_run_at=ANY,
            last_error=None,
        )
        event_types = [call.args[1] for call in record_event_mock.call_args_list]
        self.assertIn('order_early_recreated', event_types)


if __name__ == '__main__':
    unittest.main()
