"""
Unit tests for RivianAPI.

Run unit tests only (no network):
    python -m unittest discover -s charging_automation/tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from RivianAPI import RivianAPI  # noqa: E402


class _Resp:
    def __init__(self, status=200, payload=None, text=''):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeConfig:
    rivian_user = 'user@example.com'
    rivian_pass = 'secret'


def _make_api(tmpdir, session_exists=False):
    """Construct a RivianAPI without hitting the network."""
    session_file = os.path.join(tmpdir, 'rivian-session.json')
    if session_exists:
        with open(session_file, 'w') as f:
            json.dump({
                'appSessionToken': 'app',
                'userSessionToken': 'user',
                'csrfToken': 'csrf',
            }, f)

    with mock.patch.object(RivianAPI, 'login'), \
         mock.patch.object(RivianAPI, 'init_vehicle_info'):
        api = RivianAPI(_FakeConfig(), session_file)
    return api


class TestRivianUnit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.api = _make_api(self.tmp)

    # ---------------------------------------------------------------- login

    @mock.patch('RivianAPI.requests.post')
    def test_init_session_sets_tokens(self, mock_post):
        mock_post.side_effect = [
            _Resp(200, {'data': {'createCsrfToken': {
                'csrfToken': 'csrf', 'appSessionToken': 'app',
            }}}),
            _Resp(200, {'data': {'login': {'userSessionToken': 'user'}}}),
        ]
        self.api.init_session()
        self.assertEqual(self.api.csrf_token, 'csrf')
        self.assertEqual(self.api.app_session_token, 'app')
        self.assertEqual(self.api.user_session_token, 'user')

    @mock.patch('RivianAPI.requests.post')
    def test_init_session_http_error_returns_early(self, mock_post):
        mock_post.return_value = _Resp(500, {}, text='boom')
        self.api.init_session()
        self.assertIsNone(self.api.csrf_token)

    @mock.patch('RivianAPI.requests.post')
    def test_init_user_info_success(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {'currentUser': {
            'vehicles': [{'id': 'veh-1'}],
        }}})
        self.api.app_session_token = 'app'
        self.assertTrue(self.api.init_user_info())
        self.assertEqual(self.api.vehicle_id, 'veh-1')

    @mock.patch('RivianAPI.requests.post')
    def test_init_user_info_missing_current_user(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {}})
        self.api.app_session_token = 'app'
        self.assertFalse(self.api.init_user_info())

    @mock.patch('RivianAPI.requests.post')
    def test_init_user_info_no_vehicles(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {'currentUser': {
            'vehicles': [],
        }}})
        self.api.app_session_token = 'app'
        self.assertFalse(self.api.init_user_info())

    @mock.patch('RivianAPI.requests.post')
    def test_init_user_info_no_app_session_returns_false(self, mock_post):
        self.api.app_session_token = None
        self.assertFalse(self.api.init_user_info())
        mock_post.assert_not_called()

    @mock.patch('RivianAPI.requests.post')
    def test_login_persists_new_session(self, mock_post):
        # No session file -> first init_user_info() short-circuits (no POST).
        # Then init_session() makes 2 POSTs (CSRF + login), then init_user_info()
        # makes 1 POST and succeeds.
        mock_post.side_effect = [
            # init_session: CSRF
            _Resp(200, {'data': {'createCsrfToken': {
                'csrfToken': 'csrf', 'appSessionToken': 'app',
            }}}),
            # init_session: login
            _Resp(200, {'data': {'login': {'userSessionToken': 'user'}}}),
            # init_user_info (after login) -> success
            _Resp(200, {'data': {'currentUser': {
                'vehicles': [{'id': 'veh-1'}],
            }}}),
        ]
        self.api.login()
        self.assertEqual(self.api.vehicle_id, 'veh-1')
        with open(self.api.session_file) as f:
            saved = json.load(f)
        self.assertEqual(saved['appSessionToken'], 'app')
        self.assertEqual(saved['userSessionToken'], 'user')
        self.assertEqual(saved['csrfToken'], 'csrf')

    # -------------------------------------------------------- vehicle info

    @mock.patch('RivianAPI.requests.post')
    def test_init_vehicle_info_sets_status_and_battery(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {'vehicleState': {
            'chargerStatus': {'value': 'chrgr_sts_connected_charging'},
            'batteryLevel': {'value': 72},
        }}})
        self.api.init_vehicle_info()
        self.assertEqual(self.api.charging_status, 'chrgr_sts_connected_charging')
        self.assertEqual(self.api.battery_level, 72)

    @mock.patch('RivianAPI.requests.post')
    def test_init_vehicle_info_missing_state(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {}})
        self.api.charging_status = 'unchanged'
        self.api.init_vehicle_info()
        self.assertEqual(self.api.charging_status, 'unchanged')

    @mock.patch('RivianAPI.requests.post')
    def test_init_vehicle_info_http_error(self, mock_post):
        mock_post.return_value = _Resp(500, {}, text='boom')
        self.api.charging_status = 'unchanged'
        self.api.init_vehicle_info()
        self.assertEqual(self.api.charging_status, 'unchanged')

    # ------------------------------------------------------------ helpers

    def test_is_charging(self):
        self.api.charging_status = 'chrgr_sts_connected_charging'
        self.assertTrue(self.api.is_charging())
        self.api.charging_status = 'chrgr_sts_connected_no_chrg'
        self.assertFalse(self.api.is_charging())

    def test_is_charger_connected(self):
        for state in ('chrgr_sts_connected_charging', 'chrgr_sts_connected_no_chrg'):
            self.api.charging_status = state
            self.assertTrue(self.api.is_charger_connected())
        self.api.charging_status = 'chrgr_sts_disconnected'
        self.assertFalse(self.api.is_charger_connected())

    def test_get_battery_level(self):
        self.api.battery_level = 55
        self.assertEqual(self.api.get_battery_level(), 55)

    # ----------------------------------------------------------- schedules

    @mock.patch.object(RivianAPI, 'set_charging_schedule')
    @mock.patch.object(RivianAPI, 'get_current_schedules')
    def test_set_schedule_amps_enables_with_amps(self, mock_get, mock_set):
        mock_get.return_value = [{'location': {'latitude': 1, 'longitude': 2}}]
        self.api.set_schedule_amps(24)
        mock_set.assert_called_once()
        schedule = mock_set.call_args[0][0]
        self.assertTrue(schedule['enabled'])
        self.assertEqual(schedule['amperage'], 24)
        self.assertEqual(schedule['location'], {'latitude': 1, 'longitude': 2})

    @mock.patch.object(RivianAPI, 'set_charging_schedule')
    @mock.patch.object(RivianAPI, 'get_current_schedules')
    def test_set_schedule_off_commands_zero_amps(self, mock_get, mock_set):
        mock_get.return_value = [{'location': {}}]
        self.api.set_schedule_off()
        schedule = mock_set.call_args[0][0]
        # amperage=0 with the schedule enabled is what actually stops an
        # in-progress session; disabling the schedule does not.
        self.assertTrue(schedule['enabled'])
        self.assertEqual(schedule['amperage'], 0)

    @mock.patch.object(RivianAPI, 'set_charging_schedule')
    @mock.patch.object(RivianAPI, 'get_current_schedules')
    def test_set_schedule_no_change_skips_update(self, mock_get, mock_set):
        existing = {
            'startTime': 0,
            'duration': 1440,
            'location': {},
            'enabled': True,
            'weekDays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                         'Friday', 'Saturday', 'Sunday'],
            'amperage': 32,
        }
        mock_get.return_value = [existing]
        self.api.set_schedule_amps(32)
        mock_set.assert_not_called()

    @mock.patch.object(RivianAPI, 'get_current_schedules')
    def test_get_current_schedule_amp(self, mock_get):
        mock_get.return_value = [{'amperage': 16}]
        self.assertEqual(self.api.get_current_schedule_amp(), 16)

    @mock.patch('RivianAPI.requests.post')
    def test_set_charging_schedule_success(self, mock_post):
        mock_post.return_value = _Resp(200, {'data': {'setChargingSchedules': {
            'success': True,
        }}})
        self.api.set_charging_schedule({'amperage': 16, 'enabled': True})
        mock_post.assert_called_once()

    @mock.patch('RivianAPI.requests.post')
    def test_set_charging_schedule_http_error(self, mock_post):
        mock_post.return_value = _Resp(500, {}, text='boom')
        # Should not raise.
        self.api.set_charging_schedule({'amperage': 16, 'enabled': True})

    # -------------------------------------------------------- charge_stop

    @mock.patch('RivianAPI.requests.post')
    def test_charge_stop_tries_candidates_until_one_succeeds(self, mock_post):
        # First candidate rejected (GraphQL errors), second accepted.
        existing = {
            'startTime': 0,
            'duration': 1440,
            'location': {},
            'enabled': True,
            'weekDays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                         'Friday', 'Saturday', 'Sunday'],
            'amperage': 0,
        }
        mock_post.side_effect = [
            # candidate 1: rejected
            _Resp(400, {'errors': [{'message': 'Cannot query field'}]}),
            # candidate 2: accepted
            _Resp(200, {'data': {'setVehicleCharging': {'success': True}}}),
            # getVehicle (schedule fetch)
            _Resp(200, {'data': {'getVehicle': {
                'chargingSchedules': [existing],
            }}}),
            # setChargingSchedules (forced schedule-off)
            _Resp(200, {'data': {'setChargingSchedules': {'success': True}}}),
        ]
        self.api.vehicle_id = 'veh-1'
        self.assertTrue(self.api.charge_stop())

        # Candidate 1 hit the charging endpoint; candidate 2 hit the gateway.
        first = mock_post.call_args_list[0]
        self.assertEqual(first.args[0], RivianAPI.CHARGING_URL)
        self.assertEqual(first.kwargs['json']['operationName'], 'SetChargingSession')

        second = mock_post.call_args_list[1]
        self.assertEqual(second.args[0], RivianAPI.GATEWAY_URL)
        self.assertEqual(second.kwargs['json']['operationName'], 'SetVehicleCharging')

        # Last call: forced schedule-off on the gateway.
        last = mock_post.call_args_list[-1]
        self.assertEqual(last.args[0], RivianAPI.GATEWAY_URL)
        self.assertEqual(last.kwargs['json']['operationName'], 'SetChargingSchedule')
        schedule = last.kwargs['json']['variables']['chargingSchedules'][0]
        self.assertTrue(schedule['enabled'])
        self.assertEqual(schedule['amperage'], 0)

    @mock.patch('RivianAPI.requests.post')
    def test_charge_stop_all_candidates_rejected_returns_false(self, mock_post):
        existing = {
            'startTime': 0,
            'duration': 1440,
            'location': {},
            'enabled': True,
            'weekDays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                         'Friday', 'Saturday', 'Sunday'],
            'amperage': 0,
        }
        # Every candidate rejected, then schedule fetch + forced update.
        mock_post.side_effect = [
            _Resp(400, {'errors': [{'message': 'nope'}]}),
            _Resp(400, {'errors': [{'message': 'nope'}]}),
            _Resp(400, {'errors': [{'message': 'nope'}]}),
            _Resp(400, {'errors': [{'message': 'nope'}]}),
            _Resp(200, {'data': {'getVehicle': {
                'chargingSchedules': [existing],
            }}}),
            _Resp(200, {'data': {'setChargingSchedules': {'success': True}}}),
        ]
        self.api.vehicle_id = 'veh-1'
        self.assertFalse(self.api.charge_stop())

    @mock.patch('RivianAPI.requests.post')
    def test_charge_stop_http_error_does_not_raise(self, mock_post):
        mock_post.return_value = _Resp(500, {}, text='boom')
        self.api.vehicle_id = 'veh-1'
        # Should not raise.
        self.api.charge_stop()


@unittest.skipUnless(os.environ.get('RIVIAN_LIVE') == '1',
                     'Set RIVIAN_LIVE=1 to run live Rivian connectivity test')
class TestRivianLive(unittest.TestCase):
    """
    Read-only live Rivian test. Requires /app/config.json + /sessions/rivian-session.json.

    Does NOT modify the charging schedule. Only reads vehicle state and the
    current schedule. If the session is expired, login() will re-authenticate
    (which is unavoidable for any read) and persist the new session.
    """

    def setUp(self):
        self.api = RivianAPI(_FakeConfig(), '/sessions/rivian-session.json')

    def test_live_vehicle_state(self):
        self.assertIsNotNone(self.api.vehicle_id, 'Rivian returned no vehicle_id')
        soc = self.api.get_battery_level()
        self.assertIsNotNone(soc, 'Rivian returned no battery_level')
        self.assertGreaterEqual(soc, 0)
        self.assertLessEqual(soc, 100)

        print(f'\n[LIVE] Rivian vehicle_id={self.api.vehicle_id}')
        print(f'[LIVE] SOC={soc}% status={self.api.charging_status}')
        print(f'[LIVE] charger_connected={self.api.is_charger_connected()} '
              f'is_charging={self.api.is_charging()}')

    def test_live_current_schedule(self):
        schedules = self.api.get_current_schedules()
        self.assertIsNotNone(schedules, 'Rivian returned no schedules payload')
        if not schedules:
            print('\n[LIVE] Rivian has no charging schedules configured')
            return
        first = schedules[0]
        print(f'\n[LIVE] Rivian schedule: enabled={first.get("enabled")} '
              f'amperage={first.get("amperage")} '
              f'startTime={first.get("startTime")} '
              f'duration={first.get("duration")}')
        self.assertIn('amperage', first)
        self.assertIn('enabled', first)


if __name__ == '__main__':
    unittest.main()
