"""
Unit tests for TeslaAPI.

Run unit tests only (no network):
    python -m unittest discover -s charging_automation/tests -v

These tests mock _run_async and get_vehicle_data so no network or
tesla-fleet-api calls are made. If the tesla-fleet-api / cryptography
packages are not installed, the whole module is skipped.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import TeslaAPI as tesla_module  # noqa: E402
from TeslaAPI import TeslaAPI  # noqa: E402


def _vehicle_data(soc=50, limit=80, charging_state='Charging', amps=16):
    return {'response': {'charge_state': {
        'battery_level': soc,
        'charge_limit_soc': limit,
        'charging_state': charging_state,
        'charge_amps': amps,
    }}}


def _make_api(tmpdir, session=None):
    """Construct a TeslaAPI without touching the real /app or /sessions paths."""
    config_file = os.path.join(tmpdir, 'config.json')
    with open(config_file, 'w') as f:
        json.dump({'tesla_client_id': 'cid', 'tesla_client_secret': 'csecret'}, f)

    session_file = os.path.join(tmpdir, 'tesla-session.json')
    if session is not None:
        with open(session_file, 'w') as f:
            json.dump(session, f)

    api = TeslaAPI(config_file=config_file, session_file=session_file)
    return api


@unittest.skipUnless(tesla_module._TESLA_DEPS_AVAILABLE,
                     'tesla-fleet-api / cryptography not installed')
class TestTeslaUnit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.api = _make_api(self.tmp, session={
            'access_token': 'at',
            'refresh_token': 'rt',
            'last_known_soc': 42,
            'last_known_limit': 80,
            'vehicle_id': 'veh-1',
        })

    # ------------------------------------------------------------- config

    def test_config_loaded(self):
        self.assertEqual(self.api.client_id, 'cid')
        self.assertEqual(self.api.client_secret, 'csecret')

    def test_session_loaded(self):
        self.assertEqual(self.api.access_token, 'at')
        self.assertEqual(self.api.refresh_token, 'rt')
        self.assertEqual(self.api.last_known_soc, 42)
        self.assertEqual(self.api.last_known_limit, 80)
        self.assertEqual(self.api.vehicle_id, 'veh-1')

    def test_missing_session_defaults(self):
        api = _make_api(tempfile.mkdtemp())
        self.assertIsNone(api.access_token)
        self.assertEqual(api.last_known_soc, 0)
        self.assertEqual(api.last_known_limit, 80)

    def test_save_session_persists(self):
        self.api.last_known_soc = 55
        self.api.last_known_limit = 90
        self.api.save_session()
        with open(self.api.session_file) as f:
            saved = json.load(f)
        self.assertEqual(saved['last_known_soc'], 55)
        self.assertEqual(saved['last_known_limit'], 90)
        self.assertEqual(saved['access_token'], 'at')
        self.assertEqual(saved['vehicle_id'], 'veh-1')

    # ------------------------------------------------------- vehicle data

    def test_get_vehicle_data_caches(self):
        with mock.patch.object(self.api, '_run_async', return_value=_vehicle_data()) as m:
            first = self.api.get_vehicle_data()
            second = self.api.get_vehicle_data()
        self.assertEqual(first, second)
        self.assertEqual(m.call_count, 1)

    def test_get_vehicle_data_force_refresh(self):
        with mock.patch.object(self.api, '_run_async', return_value=_vehicle_data()) as m:
            self.api.get_vehicle_data()
            self.api.get_vehicle_data(force_refresh=True)
        self.assertEqual(m.call_count, 2)

    def test_get_battery_level_updates_cache(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(soc=63, limit=85)):
            with mock.patch.object(self.api, 'save_session') as save:
                self.assertEqual(self.api.get_battery_level(), 63)
                save.assert_called_once()
        self.assertEqual(self.api.last_known_soc, 63)
        self.assertEqual(self.api.last_known_limit, 85)

    def test_get_battery_level_asleep_returns_zero(self):
        with mock.patch.object(self.api, 'get_vehicle_data', return_value=None):
            self.assertEqual(self.api.get_battery_level(), 0)

    def test_get_vehicle_limit(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(limit=90)):
            self.assertEqual(self.api.get_vehicle_limit(), 90)

    def test_get_vehicle_limit_asleep_uses_cache(self):
        self.api.last_known_limit = 77
        with mock.patch.object(self.api, 'get_vehicle_data', return_value=None):
            self.assertEqual(self.api.get_vehicle_limit(), 77)

    def test_get_cached_battery_level(self):
        self.assertEqual(self.api.get_cached_battery_level(), 42)

    def test_get_cached_vehicle_limit(self):
        self.assertEqual(self.api.get_cached_vehicle_limit(), 80)

    # ------------------------------------------------------------ state

    def test_is_charging_true(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(charging_state='Charging')):
            self.assertTrue(self.api.is_charging())

    def test_is_charging_false(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(charging_state='Stopped')):
            self.assertFalse(self.api.is_charging())

    def test_is_charging_asleep_returns_false(self):
        with mock.patch.object(self.api, 'get_vehicle_data', return_value=None):
            self.assertFalse(self.api.is_charging())

    def test_is_charger_connected_true(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(charging_state='Charging')):
            self.assertTrue(self.api.is_charger_connected())

    def test_is_charger_connected_disconnected(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(charging_state='Disconnected')):
            self.assertFalse(self.api.is_charger_connected())

    def test_is_charger_connected_asleep_assumes_true(self):
        with mock.patch.object(self.api, 'get_vehicle_data', return_value=None):
            self.assertTrue(self.api.is_charger_connected())

    def test_get_current_schedule_amp(self):
        with mock.patch.object(self.api, 'get_vehicle_data',
                               return_value=_vehicle_data(amps=24)):
            self.assertEqual(self.api.get_current_schedule_amp(), 24)

    def test_get_current_schedule_amp_asleep(self):
        with mock.patch.object(self.api, 'get_vehicle_data', return_value=None):
            self.assertEqual(self.api.get_current_schedule_amp(), 0)

    # ---------------------------------------------------------- commands

    def test_set_charging_amps_clamps_low(self):
        with mock.patch.object(self.api, '_run_async', return_value=None) as m:
            self.api.set_charging_amps(1)
        m.assert_called_once()

    def test_set_charging_amps_clamps_high(self):
        with mock.patch.object(self.api, '_run_async', return_value=None) as m:
            self.api.set_charging_amps(999)
        m.assert_called_once()

    def test_charge_start_calls_run_async(self):
        with mock.patch.object(self.api, '_run_async', return_value=None) as m:
            self.api.charge_start()
        m.assert_called_once()

    def test_charge_stop_calls_run_async(self):
        with mock.patch.object(self.api, '_run_async', return_value=None) as m:
            self.api.charge_stop()
        m.assert_called_once()

    def test_set_schedule_aliases(self):
        with mock.patch.object(self.api, 'set_charging_amps') as m:
            self.api.set_schedule_amps(20)
        m.assert_called_once_with(20)

        with mock.patch.object(self.api, 'charge_stop') as m:
            self.api.set_schedule_off()
        m.assert_called_once()

        with mock.patch.object(self.api, 'set_charging_amps') as m:
            self.api.set_schedule_default()
        m.assert_called_once_with(TeslaAPI.AMPS_MAX)

    # ------------------------------------------------------------ wake_up

    def test_wake_up_skips_when_awake(self):
        with mock.patch.object(self.api, 'get_battery_level', return_value=50):
            with mock.patch.object(self.api, '_run_async') as m:
                self.assertTrue(self.api.wake_up())
        m.assert_not_called()

    def test_wake_up_sends_command_and_invalidates_cache(self):
        self.api._cached_vehicle_data = {'stale': True}
        self.api._cache_populated = True
        with mock.patch.object(self.api, 'get_battery_level', return_value=0):
            with mock.patch.object(self.api, '_run_async', return_value=True) as m:
                with mock.patch('TeslaAPI.time.sleep'):
                    self.assertTrue(self.api.wake_up())
        m.assert_called_once()
        self.assertIsNone(self.api._cached_vehicle_data)
        self.assertFalse(self.api._cache_populated)


@unittest.skipUnless(os.environ.get('TESLA_LIVE') == '1',
                     'Set TESLA_LIVE=1 to run live Tesla connectivity test')
@unittest.skipUnless(tesla_module._TESLA_DEPS_AVAILABLE,
                     'tesla-fleet-api / cryptography not installed')
class TestTeslaLive(unittest.TestCase):
    """
    Read-only live Tesla test. Requires /app/config.json + /sessions/tesla-session.json.

    Does NOT wake the car, start/stop charging, or change amps. If the car is
    asleep, get_vehicle_data() returns None and the test reports cached values.
    """

    def setUp(self):
        self.api = TeslaAPI('/app/config.json', '/sessions/tesla-session.json')

    def test_live_vehicle_data(self):
        data = self.api.get_vehicle_data()
        if data is None:
            # Asleep or offline — verify the cache fallbacks still work.
            print('\n[LIVE] Tesla asleep/offline; using cached values')
            print(f'[LIVE] cached SOC   = {self.api.get_cached_battery_level()}%')
            print(f'[LIVE] cached limit = {self.api.get_cached_vehicle_limit()}%')
            self.assertGreaterEqual(self.api.get_cached_battery_level(), 0)
            self.assertGreaterEqual(self.api.get_cached_vehicle_limit(), 0)
            return

        charge_state = data.get('response', {}).get('charge_state', {})
        soc = charge_state.get('battery_level')
        limit = charge_state.get('charge_limit_soc')
        state = charge_state.get('charging_state')
        amps = charge_state.get('charge_amps')

        self.assertIsNotNone(soc, 'Tesla returned no battery_level')
        self.assertIsNotNone(limit, 'Tesla returned no charge_limit_soc')
        self.assertGreaterEqual(soc, 0)
        self.assertLessEqual(soc, 100)
        self.assertGreaterEqual(limit, 0)
        self.assertLessEqual(limit, 100)

        print(f'\n[LIVE] Tesla SOC={soc}% limit={limit}% state={state} amps={amps}')
        print(f'[LIVE] charger_connected={self.api.is_charger_connected()} '
              f'is_charging={self.api.is_charging()}')


if __name__ == '__main__':
    unittest.main()
