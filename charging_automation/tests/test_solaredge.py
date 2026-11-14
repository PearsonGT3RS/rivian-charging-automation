"""
Unit tests for SolarEdgeAPI (v2 OAuth).

Run unit tests only (no network):
    python -m unittest discover -s charging_automation/tests -v

Run live connectivity test (requires /app/config.json + token file):
    SOLAREDGE_LIVE=1 python -m unittest charging_automation.tests.test_solaredge -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# Allow running from repo root without installing the package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from SolarEdgeAPI import SolarEdgeAPI, PowerFlow, LoadUnavailableError  # noqa: E402


def _write_creds(tmpdir):
    path = os.path.join(tmpdir, 'config.json')
    with open(path, 'w') as f:
        json.dump({
            'solaredge-client-id': 'cid',
            'solaredge-client-secret': 'csecret',
            'solaredge-site-id': '12345',
            'solaredge-redirect-uri': 'http://localhost:8080/oauth/callback',
        }, f)
    return path


def _write_token(tmpdir, access='at', refresh='rt', expires_in=7200):
    path = os.path.join(tmpdir, 'token.json')
    with open(path, 'w') as f:
        json.dump({
            'access_token': access,
            'refresh_token': refresh,
            'access_expires_at': time.time() + expires_in,
        }, f)
    return path


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f'{self.status_code}')


class TestSolarEdgeUnit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.creds = _write_creds(self.tmp)
        self.token = _write_token(self.tmp)

    def test_loads_token_file(self):
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        self.assertEqual(api._access_token, 'at')
        self.assertEqual(api._refresh_token, 'rt')

    def test_missing_token_file_warns_but_constructs(self):
        api = SolarEdgeAPI(self.creds, token_file=os.path.join(self.tmp, 'nope.json'))
        self.assertIsNone(api._access_token)

    @mock.patch('SolarEdgeAPI.requests.post')
    def test_refresh_rotates_refresh_token(self, mock_post):
        mock_post.return_value = _Resp(200, {
            'access_token': 'new_at',
            'refresh_token': 'new_rt',
            'expires_in': 7200,
        })
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        api._access_expires_at = 0  # force refresh
        api._ensure_token()

        self.assertEqual(api._access_token, 'new_at')
        self.assertEqual(api._refresh_token, 'new_rt')

        # Newest refresh token must be persisted.
        with open(self.token) as f:
            saved = json.load(f)
        self.assertEqual(saved['refresh_token'], 'new_rt')
        self.assertEqual(saved['access_token'], 'new_at')

    @mock.patch('SolarEdgeAPI.requests.post')
    def test_refresh_without_refresh_token_raises(self, mock_post):
        api = SolarEdgeAPI(self.creds, token_file=os.path.join(self.tmp, 'nope.json'))
        with self.assertRaises(RuntimeError):
            api._refresh_access_token()
        mock_post.assert_not_called()

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_get_current_power_parses_latest_sample(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'values': [
                {'date': '2026-01-01T12:00:00', 'value': 1000},
                {'date': '2026-01-01T12:15:00', 'value': 2500},
            ]
        })
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        self.assertEqual(api.get_current_power(), 2500.0)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_get_current_power_empty_values_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {'values': []})
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        self.assertIsNone(api.get_current_power())

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_401_forces_refresh_and_retries(self, mock_get):
        # First call: 401. Second call: success.
        mock_get.side_effect = [
            _Resp(401, {}),
            _Resp(200, {'values': [{'value': 42}]}),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        with mock.patch.object(api, '_refresh_access_token') as mock_refresh:
            def _do_refresh():
                api._access_token = 'refreshed'
                api._access_expires_at = time.time() + 7200
            mock_refresh.side_effect = _do_refresh
            self.assertEqual(api.get_current_power(), 42.0)
            mock_refresh.assert_called_once()

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_with_load_provider(self, mock_get):
        mock_get.return_value = _Resp(200, {'values': [{'value': 3000}]})
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow(load_provider=lambda: 5000.0)
        self.assertIsInstance(pf, PowerFlow)
        self.assertEqual(pf.pv, 3000.0)
        self.assertEqual(pf.load, 5000.0)
        self.assertEqual(pf.grid, 2000.0)  # importing 2kW

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_load_provider_none_raises(self, mock_get):
        mock_get.return_value = _Resp(200, {'values': [{'value': 3000}]})
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        with self.assertRaises(LoadUnavailableError):
            api.get_current_power_flow(load_provider=lambda: None)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_without_provider_assumes_balanced(self, mock_get):
        # /power returns PV; /meters/telemetry returns no consumption meter.
        mock_get.side_effect = [
            _Resp(200, {'values': [{'value': 3000}]}),
            _Resp(200, {}),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow()
        self.assertEqual(pf.pv, 3000.0)
        self.assertEqual(pf.load, 3000.0)
        self.assertEqual(pf.grid, 0.0)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_without_provider_uses_meter_telemetry(self, mock_get):
        # /power -> PV; /meters/telemetry -> consumption + export meters.
        mock_get.side_effect = [
            _Resp(200, {'values': [{'value': 5000}]}),
            _Resp(200, {
                'meters': {
                    'M-PROD-001': {
                        'productionPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 5000},
                        ]},
                    },
                    'M-CONS-001': {
                        'consumptionPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 3000},
                        ]},
                    },
                    'M-EXPORT-001': {
                        'exportPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 2000},
                        ]},
                    },
                },
            }),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow()
        self.assertEqual(pf.pv, 5000.0)
        self.assertEqual(pf.load, 3000.0)
        # exportPower positive = export -> grid negative (import convention).
        self.assertEqual(pf.grid, -2000.0)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_without_provider_uses_import_power(self, mock_get):
        mock_get.side_effect = [
            _Resp(200, {'values': [{'value': 1000}]}),
            _Resp(200, {
                'meters': {
                    'M-CONS-001': {
                        'consumptionPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 4000},
                        ]},
                    },
                    'M-IMPORT-001': {
                        'importPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 3000},
                        ]},
                    },
                },
            }),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow()
        self.assertEqual(pf.load, 4000.0)
        self.assertEqual(pf.grid, 3000.0)  # importing 3kW

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_without_provider_derives_grid_when_meters_missing(
            self, mock_get):
        mock_get.side_effect = [
            _Resp(200, {'values': [{'value': 5000}]}),
            _Resp(200, {
                'meters': {
                    'M-CONS-001': {
                        'consumptionPower': {'unit': 'W', 'values': [
                            {'timestamp': '2026-01-01T12:00:00Z', 'value': 3000},
                        ]},
                    },
                },
            }),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow()
        self.assertEqual(pf.load, 3000.0)
        self.assertEqual(pf.grid, -2000.0)  # exporting 2000W

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_flow_without_provider_telemetry_error_falls_back(
            self, mock_get):
        mock_get.side_effect = [
            _Resp(200, {'values': [{'value': 3000}]}),
            _Resp(500, {}),
        ]
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        pf = api.get_current_power_flow()
        self.assertEqual(pf.pv, 3000.0)
        self.assertEqual(pf.load, 3000.0)
        self.assertEqual(pf.grid, 0.0)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_details_picks_latest_sample_across_meters(self, mock_get):
        # Two meters report consumptionPower; the newest timestamp wins.
        mock_get.return_value = _Resp(200, {
            'meters': {
                'M-CONS-001': {
                    'consumptionPower': {'unit': 'W', 'values': [
                        {'timestamp': '2026-01-01T12:00:00Z', 'value': 1000},
                        {'timestamp': '2026-01-01T12:15:00Z', 'value': 2000},
                    ]},
                },
                'M-CONS-002': {
                    'consumptionPower': {'unit': 'W', 'values': [
                        {'timestamp': '2026-01-01T12:10:00Z', 'value': 1500},
                    ]},
                },
            },
        })
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        load, grid = api.get_power_details()
        self.assertEqual(load, 2000.0)
        self.assertIsNone(grid)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_details_skips_null_samples(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'meters': {
                'M-CONS-001': {
                    'consumptionPower': {'unit': 'W', 'values': [
                        {'timestamp': '2026-01-01T12:00:00Z', 'value': 1000},
                        {'timestamp': '2026-01-01T12:15:00Z', 'value': None},
                    ]},
                },
            },
        })
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        load, _ = api.get_power_details()
        self.assertEqual(load, 1000.0)

    @mock.patch('SolarEdgeAPI.requests.get')
    def test_power_details_empty_meters_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {'meters': {}})
        api = SolarEdgeAPI(self.creds, token_file=self.token)
        self.assertEqual(api.get_power_details(), (None, None))


@unittest.skipUnless(os.environ.get('SOLAREDGE_LIVE') == '1',
                     'Set SOLAREDGE_LIVE=1 to run live SolarEdge connectivity test')
class TestSolarEdgeLive(unittest.TestCase):
    """Hits the real SolarEdge v2 API. Requires /app/config.json + token file."""

    def test_live_current_power(self):
        api = SolarEdgeAPI('/app/config.json')
        power = api.get_current_power()
        self.assertIsNotNone(power, 'SolarEdge returned no power sample')
        self.assertGreaterEqual(power, 0.0)
        print(f'\n[LIVE] SolarEdge current PV power: {power:.0f} W')

    def test_live_power_flow_with_ha_load(self):
        from HomeAssistantAPI import HomeAssistantAPI
        with open('/app/config.json') as f:
            cfg = json.load(f)
        ha = HomeAssistantAPI(cfg['homeassistant-url'], cfg['homeassistant-token'])
        entities = cfg['emporia-load-entities']
        api = SolarEdgeAPI('/app/config.json')
        pf = api.get_current_power_flow(
            load_provider=lambda: ha.get_sensor_sum(entities)
        )
        self.assertIsNotNone(pf)
        print(f'\n[LIVE] pv={pf.pv:.0f}W load={pf.load:.0f}W grid={pf.grid:.0f}W')
