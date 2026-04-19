import logging
import math
from datetime import datetime
from enum import Enum
from Config import Config
from RivianAPI import RivianAPI
from TeslaAPI import TeslaAPI
from SolarEdgeAPI import SolarEdgeAPI

logger = logging.getLogger(__name__)

SOC_THRESHOLD = 50           # % state of charge threshold for allocation rules
VOLTS = 240                  # nominal charging voltage
CHANGE_THRESHOLD_WATTS = 500 # ignore fluctuations smaller than ~2A Rivian step
RIVIAN_MIN_WATTS = RivianAPI.AMPS_MIN * VOLTS  # 8A * 240V = 1920W
TESLA_MIN_WATTS = TeslaAPI.AMPS_MIN * VOLTS    # 5A * 240V = 1200W


class AutomationMode(Enum):
    OFF = 0          # automation disabled
    DEFAULT = 1      # solar during day, charge to limit at night
    SOLAR_ONLY = 2   # solar only, no night charging


def is_night_time(config):
    current_hour = datetime.now().hour
    logger.info('Start Night: %d ; End Night: %d', config.night_time_start, config.night_time_end)
    return current_hour < config.night_time_end or current_hour >= config.night_time_start


def get_automation_mode(hubitat):
    if not hubitat:
        return AutomationMode.DEFAULT
    if not hubitat.is_automation_on():
        return AutomationMode.OFF
    return AutomationMode.DEFAULT if hubitat.is_night_charging_on() else AutomationMode.SOLAR_ONLY


def get_night_charging_limit(hubitat):
    if not hubitat:
        return 50
    return hubitat.get_night_charging_limit()


def watts_to_amps(watts, increment):
    """Convert watts to amps, rounding DOWN to the nearest valid increment to avoid grid draw."""
    return math.floor((watts / VOLTS) / increment) * increment


def allocate_solar_watts(solar_for_ev, rivian_soc, tesla_soc, rivian_connected, tesla_connected):
    """
    Split available solar watts between vehicles using SOC-based rules.

    Rules:
      1. Never draw from grid; never export more than necessary.
      2. Both vehicles < 50% SOC: 50/50 split.
      3. One vehicle > 50% SOC: 75% to lower-SOC vehicle, 25% to higher.
      4. Both vehicles > 50% SOC and no solar surplus: stop charging.

    If a vehicle's allocated share is below its hardware minimum, those
    watts are reassigned to the other vehicle to maximize solar use.

    Returns (rivian_watts, tesla_watts).
    """
    total = max(0.0, solar_for_ev)

    if not rivian_connected and not tesla_connected:
        return 0.0, 0.0

    # Single vehicle: all available solar goes to it
    if rivian_connected and not tesla_connected:
        if rivian_soc > SOC_THRESHOLD and solar_for_ev <= 0:
            return 0.0, 0.0
        return total, 0.0

    if tesla_connected and not rivian_connected:
        if tesla_soc > SOC_THRESHOLD and solar_for_ev <= 0:
            return 0.0, 0.0
        return 0.0, total

    # Both connected — Rule 4: stop if both adequately charged and no surplus
    if rivian_soc > SOC_THRESHOLD and tesla_soc > SOC_THRESHOLD and solar_for_ev <= 0:
        return 0.0, 0.0

    if total == 0:
        return 0.0, 0.0

    # Determine split ratio by SOC
    if rivian_soc <= SOC_THRESHOLD and tesla_soc <= SOC_THRESHOLD:
        rivian_ratio, tesla_ratio = 0.5, 0.5        # Rule 2: even split
    elif rivian_soc <= tesla_soc:
        rivian_ratio, tesla_ratio = 0.75, 0.25      # Rule 3: Rivian has lower SOC
    else:
        rivian_ratio, tesla_ratio = 0.25, 0.75      # Rule 3: Tesla has lower SOC

    rivian_w = total * rivian_ratio
    tesla_w = total * tesla_ratio

    # If a vehicle's share is below its minimum charging power, reassign to the other
    rivian_usable = rivian_w >= RIVIAN_MIN_WATTS
    tesla_usable = tesla_w >= TESLA_MIN_WATTS

    if not rivian_usable and not tesla_usable:
        return 0.0, 0.0
    if not rivian_usable:
        return 0.0, min(total, TeslaAPI.AMPS_MAX * VOLTS)
    if not tesla_usable:
        return min(total, RivianAPI.AMPS_MAX * VOLTS), 0.0

    return rivian_w, tesla_w


def apply_charging(vehicle, target_watts, increment, vehicle_name):
    """Set charging amps for a vehicle based on target watts, or stop if below minimum."""
    target_amps = watts_to_amps(target_watts, increment)
    target_amps = min(target_amps, vehicle.AMPS_MAX)
    logger.info('%s: target %.0fW → %dA', vehicle_name, target_watts, target_amps)
    if target_amps < vehicle.AMPS_MIN:
        logger.info('%s: %dA below min %dA, turning off', vehicle_name, target_amps, vehicle.AMPS_MIN)
        vehicle.set_schedule_off()
    else:
        vehicle.set_schedule_amps(target_amps)


def run_charging_automation():
    logger.info('Running charging automation cycle...')

    hubitat = None  # Replace with HubitatAPI('hubitat-config.json') to enable

    mode = get_automation_mode(hubitat)
    logger.info('Automation mode: %s', mode)

    if mode == AutomationMode.OFF:
        logger.info('Automation is OFF')
        return

    config = Config('/app/config.json')
    rivian = RivianAPI(config, '/app/sessions/rivian-session.json')
    tesla = TeslaAPI('/app/config.json', '/app/sessions/tesla-session.json')
    solaredge = SolarEdgeAPI('/app/config.json')

    rivian_connected = rivian.is_charger_connected()
    tesla_connected = tesla.is_charger_connected()
    logger.info('Rivian connected: %s  Tesla connected: %s', rivian_connected, tesla_connected)

    if not rivian_connected and not tesla_connected:
        logger.info('No chargers plugged in')
        if hubitat:
            hubitat.set_info_message('Charging: not plugged in', 0, 0)
        return

    # Night time handling
    if is_night_time(config):
        if mode == AutomationMode.SOLAR_ONLY:
            logger.info('Solar-only: disabling all charging at night')
            if rivian_connected:
                rivian.set_schedule_off()
            if tesla_connected:
                tesla.set_schedule_off()
            if hubitat:
                hubitat.set_info_message('Charging: disabled (night off)', 0, 0)
        elif mode == AutomationMode.DEFAULT:
            charging_limit = get_night_charging_limit(hubitat)
            for vehicle, name in [(rivian, 'Rivian'), (tesla, 'Tesla')]:
                connected = rivian_connected if vehicle is rivian else tesla_connected
                if not connected:
                    continue
                soc = vehicle.get_battery_level()
                if soc < charging_limit:
                    logger.info('%s: charging to %d%% at night (now %d%%)', name, charging_limit, round(soc))
                    vehicle.set_schedule_default()
                else:
                    logger.info('%s: at %d%%, turning off at night', name, round(soc))
                    vehicle.set_schedule_off()
        return

    # Daytime solar charging
    power_flow = solaredge.get_current_power_flow()
    if power_flow is None:
        logger.error('Failed to get power flow data')
        return

    # available_power: positive = exporting surplus, negative = importing from grid
    available_power = -power_flow.grid
    logger.info('PV=%.0fW  Load=%.0fW  Grid=%.0fW  Available=%.0fW',
                power_flow.pv, power_flow.load, power_flow.grid, available_power)

    # Watts currently consumed by actively charging vehicles
    current_power = 0.0
    if rivian_connected and rivian.is_charging():
        current_power += rivian.get_current_schedule_amp() * VOLTS
    if tesla_connected and tesla.is_charging():
        current_power += tesla.get_current_schedule_amp() * VOLTS
    logger.info('Current EV charging: %.0fW', current_power)

    # Solar energy available for EVs = what they draw now + any surplus (or minus deficit)
    solar_for_ev = current_power + available_power
    logger.info('Solar for EVs: %.0fW', solar_for_ev)

    # Use 100% SOC for disconnected vehicles so they're treated as "over threshold"
    rivian_soc = rivian.get_battery_level() if rivian_connected else 100
    tesla_soc = tesla.get_battery_level() if tesla_connected else 100
    logger.info('Rivian SOC: %d%%  Tesla SOC: %d%%', rivian_soc, tesla_soc)

    # Rule 4: must stop immediately if both vehicles are adequately charged and no surplus
    both_over = rivian_soc > SOC_THRESHOLD and tesla_soc > SOC_THRESHOLD
    must_stop = both_over and solar_for_ev <= 0

    # Skip insignificant power fluctuations unless a forced stop is required
    if not must_stop and abs(available_power) < CHANGE_THRESHOLD_WATTS:
        logger.info('Power change %.0fW below threshold, skipping update', available_power)
        return

    rivian_w, tesla_w = allocate_solar_watts(
        solar_for_ev, rivian_soc, tesla_soc, rivian_connected, tesla_connected
    )
    logger.info('Allocated: Rivian=%.0fW  Tesla=%.0fW', rivian_w, tesla_w)

    if rivian_connected:
        apply_charging(rivian, rivian_w, 2, 'Rivian')
    if tesla_connected:
        apply_charging(tesla, tesla_w, 1, 'Tesla')

    logger.info('Automation cycle complete')
