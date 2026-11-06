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


if __name__ == '__main__':
    unittest.main()
