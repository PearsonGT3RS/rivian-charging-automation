"""
Unit tests for ChargingAutomation pure-logic helpers.

Run unit tests only (no network):
    python -m unittest discover -s charging_automation/tests -v
"""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import ChargingAutomation as ca  # noqa: E402
from ChargingAutomation import (  # noqa: E402
    AutomationMode,
    CycleResult,
    VOLTS,
    RIVIAN_MIN_WATTS,
    TESLA_MIN_WATTS,
    allocate_solar_watts,
    get_automation_mode,
    is_night_time,
    run_charging_automation,
    validate_emporia_config,
    watts_to_amps,
)


class _FakeConfig:
    def __init__(self, start, end):
        self.night_time_start = start
        self.night_time_end = end


class _FakeHubitat:
    def __init__(self, automation_on=True, night_on=True, limit=80):
        self._automation_on = automation_on
        self._night_on = night_on
        self._limit = limit

    def is_automation_on(self):
        return self._automation_on

    def is_night_charging_on(self):
        return self._night_on

    def get_night_charging_limit(self):
        return self._limit


class TestWattsToAmps(unittest.TestCase):

    def test_rounds_down_to_increment(self):
        # 5000W / 240V = 20.83A -> floor to 20A at 2A increment
        self.assertEqual(watts_to_amps(5000, 2), 20)

    def test_rounds_down_to_one_amp(self):
        # 5000W / 240V = 20.83A -> floor to 20A at 1A increment
        self.assertEqual(watts_to_amps(5000, 1), 20)

    def test_zero_watts(self):
        self.assertEqual(watts_to_amps(0, 1), 0)

    def test_below_one_amp(self):
        # 100W / 240V = 0.41A -> 0
        self.assertEqual(watts_to_amps(100, 1), 0)


class _FakeDatetime:
    """Stand-in for the datetime class so we can control .now()."""

    _now = None

    @classmethod
    def now(cls):
        return cls._now


class TestIsNightTime(unittest.TestCase):

    def _at_hour(self, hour):
        fake_now = datetime(2026, 1, 1, hour, 0, 0)
        _FakeDatetime._now = fake_now
        return mock.patch.object(ca, 'datetime', _FakeDatetime)

    def test_wraps_midnight(self):
        cfg = _FakeConfig(start=21, end=7)
        for hour, expected in [(20, False), (21, True), (23, True),
                               (0, True), (6, True), (7, False), (12, False)]:
            with self._at_hour(hour):
                self.assertEqual(is_night_time(cfg), expected, f'hour={hour}')

    def test_same_day_window(self):
        cfg = _FakeConfig(start=1, end=5)
        for hour, expected in [(0, False), (1, True), (3, True),
                               (4, True), (5, False), (12, False)]:
            with self._at_hour(hour):
                self.assertEqual(is_night_time(cfg), expected, f'hour={hour}')


class TestGetAutomationMode(unittest.TestCase):

    def test_no_hubitat_defaults(self):
        self.assertEqual(get_automation_mode(None), AutomationMode.DEFAULT)

    def test_automation_off(self):
        h = _FakeHubitat(automation_on=False)
        self.assertEqual(get_automation_mode(h), AutomationMode.OFF)

    def test_solar_only(self):
        h = _FakeHubitat(automation_on=True, night_on=False)
        self.assertEqual(get_automation_mode(h), AutomationMode.SOLAR_ONLY)

    def test_default(self):
        h = _FakeHubitat(automation_on=True, night_on=True)
        self.assertEqual(get_automation_mode(h), AutomationMode.DEFAULT)


class TestAllocateSolarWatts(unittest.TestCase):

    def test_no_vehicles_connected(self):
        self.assertEqual(
            allocate_solar_watts(5000, 50, 50, False, False), (0.0, 0.0)
        )

    def test_only_rivian_connected_gets_all(self):
        r, t = allocate_solar_watts(5000, 50, 100, True, False)
        self.assertEqual(r, 5000)
        self.assertEqual(t, 0.0)

    def test_only_tesla_connected_gets_all(self):
        r, t = allocate_solar_watts(5000, 100, 50, False, True)
        self.assertEqual(r, 0.0)
        self.assertEqual(t, 5000)

    def test_rivian_full_gets_nothing(self):
        r, t = allocate_solar_watts(5000, 100, 50, True, False)
        self.assertEqual((r, t), (0.0, 0.0))

    def test_both_below_threshold_even_split(self):
        r, t = allocate_solar_watts(8000, 40, 40, True, True)
        self.assertEqual(r, 4000)
        self.assertEqual(t, 4000)

    def test_rivian_lower_soc_gets_75(self):
        r, t = allocate_solar_watts(8000, 30, 70, True, True)
        self.assertEqual(r, 6000)
        self.assertEqual(t, 2000)

    def test_tesla_lower_soc_gets_75(self):
        r, t = allocate_solar_watts(8000, 70, 30, True, True)
        self.assertEqual(r, 2000)
        self.assertEqual(t, 6000)

    def test_both_full_gets_nothing(self):
        self.assertEqual(
            allocate_solar_watts(8000, 100, 100, True, True), (0.0, 0.0)
        )

    def test_both_over_threshold_no_surplus_stops(self):
        self.assertEqual(
            allocate_solar_watts(0, 60, 60, True, True), (0.0, 0.0)
        )

    def test_rivian_share_below_min_reassigned_to_tesla(self):
        # 25% of 4000 = 1000W < RIVIAN_MIN_WATTS (1920W) -> all to Tesla
        r, t = allocate_solar_watts(4000, 70, 30, True, True)
        self.assertEqual(r, 0.0)
        self.assertEqual(t, 4000)

    def test_tesla_share_below_min_reassigned_to_rivian(self):
        # 25% of 4000 = 1000W < TESLA_MIN_WATTS (1200W) -> all to Rivian
        r, t = allocate_solar_watts(4000, 30, 70, True, True)
        self.assertEqual(r, 4000)
        self.assertEqual(t, 0.0)

    def test_both_shares_below_min_stops(self):
        # 50/50 of 2000 = 1000W each; both below their minimums
        self.assertEqual(
            allocate_solar_watts(2000, 40, 40, True, True), (0.0, 0.0)
        )

    def test_negative_solar_clamped_to_zero(self):
        self.assertEqual(
            allocate_solar_watts(-500, 40, 40, True, True), (0.0, 0.0)
        )

    def test_min_watts_constants(self):
        self.assertEqual(RIVIAN_MIN_WATTS, 8 * VOLTS)
        self.assertEqual(TESLA_MIN_WATTS, 5 * VOLTS)


class TestConfigEmporiaEntities(unittest.TestCase):
    """Verify Config loads the per-EV Emporia entity keys."""

    def _write_config(self, tmpdir, extra):
        import json
        import tempfile
        path = os.path.join(tempfile.mkdtemp(dir=tmpdir), 'config.json')
        base = {
            'rivian-user': 'u',
            'rivian-pass': 'p',
            'solaredge-client-id': 'cid',
            'solaredge-client-secret': 'csecret',
            'solaredge-site-id': 'sid',
            'night-time-start': 21,
            'night-time-end': 7,
        }
        base.update(extra)
        with open(path, 'w') as f:
            json.dump(base, f)
        return path

    def test_ev_entities_loaded(self):
        import tempfile
        from Config import Config
        path = self._write_config(tempfile.mkdtemp(), {
            'emporia-rivian-entity': 'sensor.rivian_charger_power_minute_average',
            'emporia-tesla-entity': 'sensor.external_lighting_power_minute_average',
        })
        cfg = Config(path)
        self.assertEqual(
            cfg.emporia_rivian_entity,
            'sensor.rivian_charger_power_minute_average',
        )
        self.assertEqual(
            cfg.emporia_tesla_entity,
            'sensor.external_lighting_power_minute_average',
        )

    def test_ev_entities_default_to_none(self):
        import tempfile
        from Config import Config
        path = self._write_config(tempfile.mkdtemp(), {})
        cfg = Config(path)
        self.assertIsNone(cfg.emporia_rivian_entity)
        self.assertIsNone(cfg.emporia_tesla_entity)


class TestValidateEmporiaConfig(unittest.TestCase):
    """Verify the startup warning for per-EV entities missing from load entities."""

    def _cfg(self, load_entities, rivian_entity=None, tesla_entity=None):
        return _FakeConfigWithEmporia(
            rivian_entity=rivian_entity,
            tesla_entity=tesla_entity,
            load_entities=load_entities,
        )

    def test_no_warning_when_both_present(self):
        cfg = self._cfg(
            ['sensor.load_a', 'sensor.rivian', 'sensor.tesla'],
            rivian_entity='sensor.rivian',
            tesla_entity='sensor.tesla',
        )
        with self.assertNoLogs(ca.logger, level='WARNING'):
            validate_emporia_config(cfg)

    def test_warns_when_rivian_missing(self):
        cfg = self._cfg(
            ['sensor.load_a', 'sensor.tesla'],
            rivian_entity='sensor.rivian',
            tesla_entity='sensor.tesla',
        )
        with self.assertLogs(ca.logger, level='WARNING') as cm:
            validate_emporia_config(cfg)
        self.assertTrue(any('emporia-rivian-entity' in m for m in cm.output))

    def test_warns_when_tesla_missing(self):
        cfg = self._cfg(
            ['sensor.load_a', 'sensor.rivian'],
            rivian_entity='sensor.rivian',
            tesla_entity='sensor.tesla',
        )
        with self.assertLogs(ca.logger, level='WARNING') as cm:
            validate_emporia_config(cfg)
        self.assertTrue(any('emporia-tesla-entity' in m for m in cm.output))

    def test_warns_for_both_missing(self):
        cfg = self._cfg(
            ['sensor.load_a'],
            rivian_entity='sensor.rivian',
            tesla_entity='sensor.tesla',
        )
        with self.assertLogs(ca.logger, level='WARNING') as cm:
            validate_emporia_config(cfg)
        self.assertTrue(any('emporia-rivian-entity' in m for m in cm.output))
        self.assertTrue(any('emporia-tesla-entity' in m for m in cm.output))

    def test_no_warning_when_no_per_ev_entities(self):
        cfg = self._cfg(['sensor.load_a'])
        with self.assertNoLogs(ca.logger, level='WARNING'):
            validate_emporia_config(cfg)

    def test_no_warning_when_no_load_entities(self):
        # Nothing to validate against; skip silently.
        cfg = self._cfg([], rivian_entity='sensor.rivian')
        with self.assertNoLogs(ca.logger, level='WARNING'):
            validate_emporia_config(cfg)


class TestCycleResult(unittest.TestCase):
    """Verify the CycleResult contract used by main.py to pick a sleep interval."""

    def test_enum_members_exist(self):
        self.assertIn('IDLE', CycleResult.__members__)
        self.assertIn('NIGHT_ACTIVE', CycleResult.__members__)
        self.assertIn('DAYTIME_ACTIVE', CycleResult.__members__)

    def test_members_are_distinct(self):
        # main.py dispatches on equality, so the members must be distinct.
        self.assertNotEqual(CycleResult.IDLE, CycleResult.NIGHT_ACTIVE)
        self.assertNotEqual(CycleResult.IDLE, CycleResult.DAYTIME_ACTIVE)
        self.assertNotEqual(CycleResult.NIGHT_ACTIVE, CycleResult.DAYTIME_ACTIVE)


class _FakePowerFlow:
    def __init__(self, pv, load, grid):
        self.pv = pv
        self.load = load
        self.grid = grid


class _FakeConfigWithEmporia:
    """Config stub exposing the fields run_charging_automation() reads."""

    def __init__(self, rivian_entity=None, tesla_entity=None,
                 load_entities=None, ha_url='http://ha', ha_token='tok',
                 night_start=21, night_end=7,
                 pv_lead_entity=None, pv_follower_entity=None):
        self.rivian_user = 'u'
        self.rivian_pass = 'p'
        self.solaredge_client_id = 'cid'
        self.solaredge_client_secret = 'csecret'
        self.solaredge_site_id = 'sid'
        self.solaredge_redirect_uri = 'http://localhost/cb'
        self.homeassistant_url = ha_url
        self.homeassistant_token = ha_token
        self.emporia_load_entities = (
            ['sensor.load_a'] if load_entities is None else load_entities
        )
        self.emporia_rivian_entity = rivian_entity
        self.emporia_tesla_entity = tesla_entity
        self.emporia_pv_lead_entity = pv_lead_entity
        self.emporia_pv_follower_entity = pv_follower_entity
        self.night_time_start = night_start
        self.night_time_end = night_end


class TestRunChargingAutomationEvDraw(unittest.TestCase):
    """
    Verify the EV-draw calculation in run_charging_automation():

        solar_for_ev = rivian_draw + tesla_draw + available_power

    where available_power = -grid, and each *_draw comes from the dedicated
    Emporia sensor (or 0 if the sensor is missing / returns None).
    """

    def _run(self, *, pv, load, grid,
             rivian_draw=None, tesla_draw=None,
             rivian_entity='sensor.rivian',
             tesla_entity='sensor.tesla',
             rivian_connected=True, tesla_connected=True,
             rivian_soc=40, tesla_soc=40):
        """Run one daytime cycle with all network calls mocked.

        Returns (rivian_w, tesla_w, solar_for_ev) captured from the
        allocate_solar_watts call.
        """
        cfg = _FakeConfigWithEmporia(
            rivian_entity=rivian_entity, tesla_entity=tesla_entity,
        )

        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = rivian_connected
        fake_rivian.get_battery_level.return_value = rivian_soc
        fake_rivian.is_charging.return_value = False
        fake_rivian.get_current_schedule_amp.return_value = 0
        fake_rivian.get_current_schedules.return_value = []

        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = tesla_connected
        fake_tesla.get_battery_level.return_value = tesla_soc
        fake_tesla.get_vehicle_limit.return_value = 100
        fake_tesla.is_charging.return_value = False
        fake_tesla.get_current_schedule_amp.return_value = 0

        fake_solaredge = mock.Mock()
        fake_solaredge.get_current_power_flow.side_effect = (
            lambda load_provider=None, pv_override=None: _FakePowerFlow(
                pv=pv, load=load, grid=grid,
            )
        )

        # HA stub: return the configured per-EV draw for the EV sensors,
        # and a fixed value for the load sensors.
        def _fake_get_sensor_value(entity_id):
            if entity_id == rivian_entity:
                return rivian_draw
            if entity_id == tesla_entity:
                return tesla_draw
            return 0.0

        fake_ha = mock.Mock()
        fake_ha.get_sensor_value.side_effect = _fake_get_sensor_value
        fake_ha.get_sensor_sum.return_value = load

        captured = {}

        def _fake_allocate(solar_for_ev, *args, **kwargs):
            captured['solar_for_ev'] = solar_for_ev
            return 0.0, 0.0

        with mock.patch.object(ca, 'Config', return_value=cfg), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI', return_value=fake_solaredge), \
             mock.patch.object(ca, 'HomeAssistantAPI', return_value=fake_ha), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT), \
             mock.patch.object(ca, 'is_night_time', return_value=False), \
             mock.patch.object(ca, 'allocate_solar_watts',
                               side_effect=_fake_allocate), \
             mock.patch.object(ca, 'apply_charging'):
            ca.run_charging_automation()

        return captured.get('solar_for_ev')

    def test_solar_for_ev_adds_ev_draw_to_surplus(self):
        # pv=8000, load=2000 -> grid=-6000 -> available=6000
        # rivian_draw=2000, tesla_draw=3000 -> solar_for_ev = 11000
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=2000, tesla_draw=3000)
        self.assertEqual(solar, 11000)

    def test_solar_for_ev_with_no_ev_draw(self):
        # EVs not drawing: solar_for_ev == available_power
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=0, tesla_draw=0)
        self.assertEqual(solar, 6000)

    def test_solar_for_ev_with_grid_import(self):
        # grid positive (importing): available_power negative
        # pv=1000, load=3000 -> grid=2000 -> available=-2000
        # rivian_draw=1500, tesla_draw=0 -> solar_for_ev = -500
        solar = self._run(pv=1000, load=3000, grid=2000,
                          rivian_draw=1500, tesla_draw=0)
        self.assertEqual(solar, -500)

    def test_missing_rivian_sensor_falls_back_to_zero(self):
        # rivian_entity=None -> rivian_draw_provider is None -> rivian_draw=0
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=9999, tesla_draw=3000,
                          rivian_entity=None)
        self.assertEqual(solar, 9000)  # 0 + 3000 + 6000

    def test_missing_tesla_sensor_falls_back_to_zero(self):
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=2000, tesla_draw=9999,
                          tesla_entity=None)
        self.assertEqual(solar, 8000)  # 2000 + 0 + 6000

    def test_none_sensor_value_falls_back_to_zero(self):
        # Sensor configured but returns None (unavailable) -> treated as 0
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=None, tesla_draw=3000)
        self.assertEqual(solar, 9000)  # 0 + 3000 + 6000

    def test_negative_sensor_value_clamped_to_zero(self):
        # A sensor reporting a negative value (e.g. a sign glitch) is clamped
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=-500, tesla_draw=3000)
        self.assertEqual(solar, 9000)  # 0 + 3000 + 6000

    def test_disconnected_vehicle_draw_ignored(self):
        # Tesla disconnected -> its sensor is not read -> tesla_draw=0
        solar = self._run(pv=8000, load=2000, grid=-6000,
                          rivian_draw=2000, tesla_draw=9999,
                          tesla_connected=False)
        self.assertEqual(solar, 8000)  # 2000 + 0 + 6000


class TestRunChargingAutomationDaytimeCycle(unittest.TestCase):
    """
    End-to-end daytime cycle with all APIs mocked.

    Verifies that run_charging_automation() reaches the allocation step,
    calls apply_charging() for each connected vehicle with the expected
    watts, and returns CycleResult.DAYTIME_ACTIVE.
    """

    def _run(self, *, pv, load, grid,
             rivian_draw, tesla_draw,
             rivian_soc=40, tesla_soc=40,
             rivian_connected=True, tesla_connected=True):
        cfg = _FakeConfigWithEmporia(
            rivian_entity='sensor.rivian',
            tesla_entity='sensor.tesla',
        )

        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = rivian_connected
        fake_rivian.get_battery_level.return_value = rivian_soc
        fake_rivian.is_charging.return_value = False
        fake_rivian.get_current_schedule_amp.return_value = 0
        fake_rivian.get_current_schedules.return_value = []

        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = tesla_connected
        fake_tesla.get_battery_level.return_value = tesla_soc
        fake_tesla.get_vehicle_limit.return_value = 100
        fake_tesla.is_charging.return_value = False
        fake_tesla.get_current_schedule_amp.return_value = 0

        fake_solaredge = mock.Mock()
        fake_solaredge.get_current_power_flow.side_effect = (
            lambda load_provider=None, pv_override=None: _FakePowerFlow(
                pv=pv, load=load, grid=grid,
            )
        )

        def _fake_get_sensor_value(entity_id):
            if entity_id == 'sensor.rivian':
                return rivian_draw
            if entity_id == 'sensor.tesla':
                return tesla_draw
            return 0.0

        fake_ha = mock.Mock()
        fake_ha.get_sensor_value.side_effect = _fake_get_sensor_value
        fake_ha.get_sensor_sum.return_value = load

        with mock.patch.object(ca, 'Config', return_value=cfg), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI', return_value=fake_solaredge), \
             mock.patch.object(ca, 'HomeAssistantAPI', return_value=fake_ha), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT), \
             mock.patch.object(ca, 'is_night_time', return_value=False), \
             mock.patch.object(ca, 'apply_charging') as mock_apply:
            result = ca.run_charging_automation()

        return result, mock_apply, fake_rivian, fake_tesla

    def test_returns_daytime_active(self):
        result, _, _, _ = self._run(
            pv=8000, load=2000, grid=-6000,
            rivian_draw=2000, tesla_draw=3000,
        )
        self.assertIs(result, CycleResult.DAYTIME_ACTIVE)

    def test_apply_charging_called_for_both_vehicles(self):
        # solar_for_ev = 2000 + 3000 + 6000 = 11000
        # Both SOC=40 (<=50) -> 50/50 split -> 5500W each
        _, mock_apply, fake_rivian, fake_tesla = self._run(
            pv=8000, load=2000, grid=-6000,
            rivian_draw=2000, tesla_draw=3000,
        )
        self.assertEqual(mock_apply.call_count, 2)
        calls = {c.args[0]: c.args[1] for c in mock_apply.call_args_list}
        self.assertEqual(calls[fake_rivian], 5500)
        self.assertEqual(calls[fake_tesla], 5500)

    def test_apply_charging_skipped_for_disconnected_vehicle(self):
        # Tesla disconnected -> only Rivian gets apply_charging()
        _, mock_apply, fake_rivian, _ = self._run(
            pv=8000, load=2000, grid=-6000,
            rivian_draw=2000, tesla_draw=0,
            tesla_connected=False,
        )
        self.assertEqual(mock_apply.call_count, 1)
        self.assertIs(mock_apply.call_args.args[0], fake_rivian)

    def test_skips_when_power_change_below_threshold(self):
        # available_power = -grid = 100W < CHANGE_THRESHOLD_WATTS (500W)
        # and not both_over -> skip update, no apply_charging calls
        _, mock_apply, _, _ = self._run(
            pv=2000, load=1900, grid=100,
            rivian_draw=0, tesla_draw=0,
        )
        self.assertEqual(mock_apply.call_count, 0)


class TestPvSourceSelection(unittest.TestCase):
    """
    Verify PV source selection: prefer HA (lead + follower), fall back to
    SolarEdge when HA PV is unavailable.
    """

    def _run(self, *, ha_lead, ha_follower, solaredge_pv,
             pv_lead_entity='sensor.pv_lead',
             pv_follower_entity='sensor.pv_follower'):
        cfg = _FakeConfigWithEmporia(
            pv_lead_entity=pv_lead_entity,
            pv_follower_entity=pv_follower_entity,
        )

        # At least one vehicle must be connected, otherwise the cycle
        # returns at the "No chargers plugged in" early exit before
        # reaching the daytime power-flow step.
        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = True
        fake_rivian.get_battery_level.return_value = 40
        fake_rivian.is_charging.return_value = False
        fake_rivian.get_current_schedule_amp.return_value = 0
        fake_rivian.get_current_schedules.return_value = []
        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = False

        def _fake_get_sensor_value(entity_id):
            if entity_id == pv_lead_entity:
                return ha_lead
            if entity_id == pv_follower_entity:
                return ha_follower
            return 0.0

        fake_ha = mock.Mock()
        fake_ha.get_sensor_value.side_effect = _fake_get_sensor_value
        fake_ha.get_sensor_sum.return_value = 1000.0

        fake_solaredge = mock.Mock()
        fake_solaredge.get_current_power.return_value = solaredge_pv
        fake_solaredge.get_current_power_flow.side_effect = (
            lambda load_provider=None, pv_override=None: _FakePowerFlow(
                pv=pv_override if pv_override is not None else solaredge_pv,
                load=1000.0,
                grid=1000.0 - (pv_override if pv_override is not None else solaredge_pv),
            )
        )

        with mock.patch.object(ca, 'Config', return_value=cfg), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI', return_value=fake_solaredge), \
             mock.patch.object(ca, 'HomeAssistantAPI', return_value=fake_ha), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT), \
             mock.patch.object(ca, 'is_night_time', return_value=False), \
             mock.patch.object(ca, 'apply_charging'):
            ca.run_charging_automation()

        return fake_solaredge.get_current_power_flow.call_args

    def test_uses_ha_pv_when_both_sensors_available(self):
        call = self._run(ha_lead=2000, ha_follower=1500, solaredge_pv=9999)
        self.assertEqual(call.kwargs['pv_override'], 3500)

    def test_falls_back_to_solaredge_when_lead_unavailable(self):
        call = self._run(ha_lead=None, ha_follower=1500, solaredge_pv=9999)
        self.assertIsNone(call.kwargs['pv_override'])

    def test_falls_back_to_solaredge_when_follower_unavailable(self):
        call = self._run(ha_lead=2000, ha_follower=None, solaredge_pv=9999)
        self.assertIsNone(call.kwargs['pv_override'])

    def test_falls_back_to_solaredge_when_pv_entities_not_configured(self):
        call = self._run(
            ha_lead=2000, ha_follower=1500, solaredge_pv=9999,
            pv_lead_entity=None, pv_follower_entity=None,
        )
        self.assertIsNone(call.kwargs['pv_override'])


class TestKeepaliveAlwaysRuns(unittest.TestCase):
    """Verify the SolarEdge keepalive refresh runs on every cycle (day and night)."""

    def _run(self, *, night):
        cfg = _FakeConfigWithEmporia()

        # At least one vehicle must be connected, otherwise the cycle
        # returns at the "No chargers plugged in" early exit before
        # reaching the keepalive call.
        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = True
        fake_rivian.get_battery_level.return_value = 40
        fake_rivian.is_charging.return_value = False
        fake_rivian.get_current_schedule_amp.return_value = 0
        fake_rivian.get_current_schedules.return_value = []
        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = False

        fake_solaredge = mock.Mock()
        fake_solaredge.get_current_power_flow.return_value = _FakePowerFlow(
            pv=0, load=0, grid=0,
        )

        with mock.patch.object(ca, 'Config', return_value=cfg), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI', return_value=fake_solaredge), \
             mock.patch.object(ca, 'HomeAssistantAPI'), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT), \
             mock.patch.object(ca, 'is_night_time', return_value=night), \
             mock.patch.object(ca, 'apply_charging'):
            ca.run_charging_automation()

        return fake_solaredge._maybe_keepalive_refresh

    def test_keepalive_runs_during_day(self):
        mock_keepalive = self._run(night=False)
        mock_keepalive.assert_called_once()

    def test_keepalive_runs_during_night(self):
        mock_keepalive = self._run(night=True)
        mock_keepalive.assert_called_once()


class TestRunChargingAutomationEarlyExits(unittest.TestCase):
    """Exercise the early-return paths of run_charging_automation() without network."""

    def test_returns_idle_when_automation_off(self):
        # get_automation_mode() returns OFF when hubitat reports automation off.
        # run_charging_automation() hardcodes hubitat=None, so we patch the
        # module-level helper to simulate OFF mode.
        with mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.OFF):
            result = run_charging_automation()
        self.assertIs(result, CycleResult.IDLE)

    def test_returns_idle_when_no_chargers_plugged_in(self):
        # Patch out the network-touching constructors so the cycle reaches the
        # "no chargers plugged in" branch without hitting any APIs.
        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = False
        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = False

        with mock.patch.object(ca, 'Config'), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI'), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT):
            result = run_charging_automation()
        self.assertIs(result, CycleResult.IDLE)

    def test_returns_idle_when_load_unavailable(self):
        # LoadUnavailableError from SolarEdge must abort the cycle safely.
        from SolarEdgeAPI import LoadUnavailableError

        fake_rivian = mock.Mock()
        fake_rivian.is_charger_connected.return_value = True
        fake_rivian.get_battery_level.return_value = 40
        fake_rivian.is_charging.return_value = False
        fake_rivian.get_current_schedule_amp.return_value = 0
        fake_rivian.get_current_schedules.return_value = []

        fake_tesla = mock.Mock()
        fake_tesla.is_charger_connected.return_value = False

        fake_solaredge = mock.Mock()
        fake_solaredge.get_current_power_flow.side_effect = LoadUnavailableError(
            'load_provider returned None'
        )
        fake_solaredge._maybe_keepalive_refresh = mock.Mock()

        cfg = _FakeConfigWithEmporia()

        with mock.patch.object(ca, 'Config', return_value=cfg), \
             mock.patch.object(ca, 'RivianAPI', return_value=fake_rivian), \
             mock.patch.object(ca, 'TeslaAPI', return_value=fake_tesla), \
             mock.patch.object(ca, 'SolarEdgeAPI', return_value=fake_solaredge), \
             mock.patch.object(ca, 'HomeAssistantAPI'), \
             mock.patch.object(ca, 'get_automation_mode',
                               return_value=AutomationMode.DEFAULT), \
             mock.patch.object(ca, 'is_night_time', return_value=False), \
             mock.patch.object(ca, 'apply_charging') as mock_apply:
            result = run_charging_automation()

        self.assertIs(result, CycleResult.IDLE)
        self.assertEqual(mock_apply.call_count, 0)


if __name__ == '__main__':
    unittest.main()
