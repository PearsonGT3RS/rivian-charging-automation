"""
Unit tests for HubitatAPI.

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

from HubitatAPI import HubitatAPI  # noqa: E402


class _Resp:
    def __init__(self, status=200, payload=None, text=''):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _write_config(tmpdir):
    path = os.path.join(tmpdir, 'hubitat-config.json')
    with open(path, 'w') as f:
        json.dump({
            'host': 'http://hubitat.local',
            'api-id': '42',
            'token': 'tok',
            'automation-on-switch-id': '100',
            'night-charge-switch-id': '200',
            'info-device-id': '300',
        }, f)
    return path


def _device_payload(attributes):
    return {'attributes': attributes}


class TestHubitatUnit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _write_config(self.tmp)
        self.h = HubitatAPI(self.cfg)

    def test_config_loaded(self):
        self.assertEqual(self.h.host, 'http://hubitat.local')
        self.assertEqual(self.h.api_id, '42')
        self.assertEqual(self.h.token, 'tok')
        self.assertEqual(self.h.on_switch_id, '100')
        self.assertEqual(self.h.night_charge_switch_id, '200')
        self.assertEqual(self.h.info_device_id, '300')

    @mock.patch('HubitatAPI.requests.get')
    def test_get_switch_attribute_returns_value(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'switch', 'currentValue': 'on'},
            {'name': 'level', 'currentValue': 80},
        ]))
        self.assertEqual(self.h.get_switch_attribute('100', 'switch'), 'on')
        self.assertEqual(self.h.get_switch_attribute('100', 'level'), 80)

    @mock.patch('HubitatAPI.requests.get')
    def test_get_switch_attribute_missing_returns_none(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'switch', 'currentValue': 'on'},
        ]))
        self.assertIsNone(self.h.get_switch_attribute('100', 'level'))

    @mock.patch('HubitatAPI.requests.get')
    def test_get_switch_attribute_http_error_returns_none(self, mock_get):
        mock_get.return_value = _Resp(500, {}, text='boom')
        self.assertIsNone(self.h.get_switch_attribute('100', 'switch'))

    @mock.patch('HubitatAPI.requests.get')
    def test_is_automation_on(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'switch', 'currentValue': 'on'},
        ]))
        self.assertTrue(self.h.is_automation_on())

    @mock.patch('HubitatAPI.requests.get')
    def test_is_automation_off(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'switch', 'currentValue': 'off'},
        ]))
        self.assertFalse(self.h.is_automation_on())

    @mock.patch('HubitatAPI.requests.get')
    def test_is_night_charging_on(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'switch', 'currentValue': 'on'},
        ]))
        self.assertTrue(self.h.is_night_charging_on())

    @mock.patch('HubitatAPI.requests.get')
    def test_get_night_charging_limit(self, mock_get):
        mock_get.return_value = _Resp(200, _device_payload([
            {'name': 'level', 'currentValue': 85},
        ]))
        self.assertEqual(self.h.get_night_charging_limit(), 85)

    @mock.patch('HubitatAPI.requests.get')
    def test_update_info_device_message_url_encodes(self, mock_get):
        mock_get.return_value = _Resp(200, {})
        self.h.update_info_device_message('hello world & more')
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs['params'], {'value': 'hello world & more'})

    @mock.patch('HubitatAPI.requests.get')
    def test_update_info_device_message_http_error_logs(self, mock_get):
        mock_get.return_value = _Resp(500, {}, text='boom')
        # Should not raise.
        self.h.update_info_device_message('msg')

    @mock.patch('HubitatAPI.requests.get')
    def test_set_info_message_formats_and_sends(self, mock_get):
        mock_get.return_value = _Resp(200, {})
        self.h.set_info_message('Charging', 16, 1234.6)
        _, kwargs = mock_get.call_args
        value = kwargs['params']['value']
        self.assertIn('Charging', value)
        self.assertIn('Amps: 16', value)
        self.assertIn('Grid: 1235W', value)
        self.assertIn('Last update:', value)


if __name__ == '__main__':
    unittest.main()
