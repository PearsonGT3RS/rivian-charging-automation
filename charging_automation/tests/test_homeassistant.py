"""
Unit tests for HomeAssistantAPI (Emporia Vue load sensors).

Run unit tests only (no network):
    python -m unittest discover -s charging_automation/tests -v

Run live connectivity test (requires /app/config.json):
    HA_LIVE=1 python -m unittest charging_automation.tests.test_homeassistant -v
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from HomeAssistantAPI import HomeAssistantAPI  # noqa: E402


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


def _state(value, unit='W'):
    return {
        'entity_id': 'sensor.test',
        'state': str(value),
        'attributes': {'unit_of_measurement': unit},
    }


class TestHomeAssistantUnit(unittest.TestCase):

    def setUp(self):
        self.ha = HomeAssistantAPI('https://ha.example.com', 'tok')

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_get_sensor_value_watts(self, mock_get):
        mock_get.return_value = _Resp(200, _state(1234.5, 'W'))
        self.assertEqual(self.ha.get_sensor_value('sensor.test'), 1234.5)

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_get_sensor_value_kw_converts_to_watts(self, mock_get):
        mock_get.return_value = _Resp(200, _state(1.5, 'kW'))
        self.assertEqual(self.ha.get_sensor_value('sensor.test'), 1500.0)

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_unavailable_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'state': 'unavailable', 'attributes': {}
        })
        self.assertIsNone(self.ha.get_sensor_value('sensor.test'))

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_unknown_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'state': 'unknown', 'attributes': {}
        })
        self.assertIsNone(self.ha.get_sensor_value('sensor.test'))

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_non_numeric_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'state': 'on', 'attributes': {}
        })
        self.assertIsNone(self.ha.get_sensor_value('sensor.test'))

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_http_error_returns_none(self, mock_get):
        mock_get.return_value = _Resp(500, {})
        self.assertIsNone(self.ha.get_sensor_value('sensor.test'))

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_sensor_sum(self, mock_get):
        mock_get.side_effect = [
            _Resp(200, _state(1000, 'W')),
            _Resp(200, _state(2500, 'W')),
        ]
        total = self.ha.get_sensor_sum(['sensor.a', 'sensor.b'])
        self.assertEqual(total, 3500.0)

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_sensor_sum_any_unavailable_returns_none(self, mock_get):
        mock_get.side_effect = [
            _Resp(200, _state(1000, 'W')),
            _Resp(200, {'state': 'unavailable', 'attributes': {}}),
        ]
        self.assertIsNone(self.ha.get_sensor_sum(['sensor.a', 'sensor.b']))

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_authorization_header_sent(self, mock_get):
        mock_get.return_value = _Resp(200, _state(1, 'W'))
        self.ha.get_sensor_value('sensor.test')
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok')

    # ------------------------------------------------ per-EV Emporia sensors

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_rivian_charger_sensor(self, mock_get):
        mock_get.return_value = _Resp(200, _state(3840, 'W'))
        value = self.ha.get_sensor_value(
            'sensor.rivian_charger_power_minute_average'
        )
        self.assertEqual(value, 3840.0)
        url = mock_get.call_args[0][0]
        self.assertIn('sensor.rivian_charger_power_minute_average', url)

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_tesla_charger_sensor(self, mock_get):
        mock_get.return_value = _Resp(200, _state(6240, 'W'))
        value = self.ha.get_sensor_value(
            'sensor.external_lighting_power_minute_average'
        )
        self.assertEqual(value, 6240.0)
        url = mock_get.call_args[0][0]
        self.assertIn('sensor.external_lighting_power_minute_average', url)

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_ev_sensor_zero_when_idle(self, mock_get):
        # An idle EV circuit reports 0W, not unavailable.
        mock_get.return_value = _Resp(200, _state(0, 'W'))
        self.assertEqual(
            self.ha.get_sensor_value(
                'sensor.rivian_charger_power_minute_average'
            ),
            0.0,
        )

    @mock.patch('HomeAssistantAPI.requests.get')
    def test_ev_sensor_unavailable_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, {
            'state': 'unavailable', 'attributes': {}
        })
        self.assertIsNone(
            self.ha.get_sensor_value(
                'sensor.external_lighting_power_minute_average'
            )
        )


@unittest.skipUnless(os.environ.get('HA_LIVE') == '1',
                     'Set HA_LIVE=1 to run live Home Assistant connectivity test')
class TestHomeAssistantLive(unittest.TestCase):
    """Hits the real Home Assistant instance. Requires /app/config.json."""

    def test_live_emporia_sensors(self):
        with open('/app/config.json') as f:
            cfg = json.load(f)
        ha = HomeAssistantAPI(cfg['homeassistant-url'], cfg['homeassistant-token'])
        entities = cfg['emporia-load-entities']
        for entity in entities:
            value = ha.get_sensor_value(entity)
            self.assertIsNotNone(value, f'{entity} unavailable')
            self.assertGreaterEqual(value, 0.0)
            print(f'\n[LIVE] {entity} = {value:.0f} W')
        total = ha.get_sensor_sum(entities)
        self.assertIsNotNone(total)
        print(f'[LIVE] Emporia total load = {total:.0f} W')

    def test_live_per_ev_sensors(self):
        """Read the per-EV Emporia sensors if configured. Skips if absent."""
        with open('/app/config.json') as f:
            cfg = json.load(f)
        ha = HomeAssistantAPI(cfg['homeassistant-url'], cfg['homeassistant-token'])

        for key in ('emporia-rivian-entity', 'emporia-tesla-entity'):
            entity = cfg.get(key)
            if not entity:
                print(f'\n[LIVE] {key} not configured; skipping')
                continue
            value = ha.get_sensor_value(entity)
            self.assertIsNotNone(value, f'{entity} unavailable')
            self.assertGreaterEqual(value, 0.0)
            print(f'[LIVE] {entity} = {value:.0f} W')
