import logging
import math
import time
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
    """Set charging amps and handle Start/Stop via Signed Commands."""
    now = time.time()
    target_amps = watts_to_amps(target_watts, increment)
    target_amps = min(target_amps, vehicle.AMPS_MAX)
    
    # Check current state (is_charging should handle offline gracefully)
    try:
        is_currently_charging = vehicle.is_charging()
    except:
        is_currently_charging = False
    
    logger.info('%s: target %.0fW → %dA (Currently Charging: %s)', 
                vehicle_name, target_watts, target_amps, is_currently_charging)

    # 1. STOP LOGIC: Below minimum and currently charging
    if target_amps < vehicle.AMPS_MIN:
        if is_currently_charging:
            # Check Hysteresis: Has it been 5 minutes since the last start/stop?
            #if (now - last_action_times[vehicle_name]) > MIN_ACTION_INTERVAL:
            logger.info(f'{vehicle_name}: Below minimum. Stopping charge.')
            if vehicle_name == "Tesla":
                vehicle.charge_stop() # Use Signed Stop for Highland
            else:
                vehicle.set_schedule_off() # Use Schedule Off for Rivian to avoid wake-up
        return

    # 2. START/UPDATE LOGIC: Above minimum
    if not is_currently_charging:
        logger.info(f'{vehicle_name}: Starting charge session.')
        if vehicle_name == "Tesla":
            vehicle.wake_up() # Ensure Highland is awake for the signed command
            vehicle.charge_start() # New Tesla Signed Command
            vehicle.set_charging_amps(target_amps) # Signed Amps
        else:
            vehicle.set_schedule_amps(target_amps) # Rivian Schedule Amps (no wake-up needed)
    else:
        # Already charging, just update the amperage (no hysteresis needed for amp changes)
        if vehicle_name == "Tesla":
            vehicle.set_charging_amps(target_amps) # Signed Amps
        else:
            vehicle.set_schedule_amps(target_amps) # Rivian Schedule Amps (no wake-up needed)


def run_charging_automation():
    logger.info('Running charging automation cycle...')

    hubitat = None  # Replace with HubitatAPI('hubitat-config.json') to enable
    # NEW: Move SOC check AFTER determining if we actually have solar
    # This prevents the Highland from being woken up for no reason
    mode = get_automation_mode(hubitat)
    logger.info('Automation mode: %s', mode)
    if mode == AutomationMode.OFF:
        logger.info('Automation is OFF')
        return
    
    config = Config('/app/config.json')
    rivian = RivianAPI(config, '/sessions/rivian-session.json')
    tesla = TeslaAPI('/app/config.json', '/sessions/tesla-session.json')
    solaredge = SolarEdgeAPI('/app/config.json')

    rivian_connected = rivian.is_charger_connected()
    tesla_connected = tesla.is_charger_connected()

    logger.info('Rivian connected: %s  Tesla connected: %s', rivian_connected, tesla_connected)

    if not rivian_connected and not tesla_connected:
        logger.info('No chargers plugged in')
        if hubitat:
            hubitat.set_info_message('Charging: not plugged in', 0, 0)
        return


    # --- NIGHT TIME HANDLING ---
    if is_night_time(config):
        if mode == AutomationMode.SOLAR_ONLY:
            logger.info('Solar-only mode: ensuring charging is stopped for the night')
            if rivian_connected:
                rivian.set_schedule_off() # Use Schedule Off for Rivian to avoid wake-up
            if tesla_connected:
                tesla.charge_stop() # Use Signed Stop for Highland
                
        elif mode == AutomationMode.DEFAULT:
            charging_limit = get_night_charging_limit(hubitat)
            
            for vehicle, name in [(rivian, 'Rivian'), (tesla, 'Tesla')]:
                connected = rivian_connected if vehicle is rivian else tesla_connected
                if not connected:
                    continue
                
                # Sleep-aware check: Wake the car if we need to check SOC for night charging
                if name == "Tesla":
                    state = tesla.get_state()
                    if state != "online":
                        logger.info('Tesla is %s. Waking for night SOC check...', state)
                        tesla.wake_up()
                        time.sleep(5)
                
                soc = vehicle.get_battery_level() or 0
                if soc < charging_limit:
                    logger.info('%s: below limit (%d%% < %d%%). Starting night charge.', name, round(soc), charging_limit)
                    if name == "Tesla":
                        vehicle.charge_start() # Use Signed Start
                        # Set to max (or your preferred night rate) using Signed Amps
                        vehicle.set_charging_amps(vehicle.AMPS_MAX)
                    else:
                        vehicle.set_schedule_amps(vehicle.AMPS_MAX) # Use Schedule Amps for Rivian
                else:
                    logger.info('%s: at limit (%d%%). Stopping night charge.', name, round(soc))
                    if name == "Tesla":
                        vehicle.charge_stop()
                    else:
                        vehicle.set_schedule_off() # Use Schedule Off for Rivian to avoid wake-up
        
        logger.info('Night-time processing complete.')
        return # Exit the function; do not proceed to solar logic
    # --- END NIGHT TIME HANDLING ---

    # Daytime Solar Logic continues here...
    # 1. Check Power Flow before polling vehicle sensors
    power_flow = solaredge.get_current_power_flow()
    if power_flow is None:
        logger.error('Failed to get power flow data')
        return

    # available_power: positive = surplus, negative = grid draw
    available_power = -power_flow.grid
    logger.info('Surplus available: %.0fW', available_power)

    # Tesla SOC (Integrating Sleep-Aware + Disconnected = 100% logic)
    tesla_soc = 100 # Default to "Full" to prevent accidental draw
    if tesla_connected:
        # Passive check of cloud state (doesn't wake car)
        tesla_state = tesla.get_state() 
        logger.info('Tesla state: %s', tesla_state)

        if tesla_state == "online":
            tesla_soc = tesla.get_battery_level() or 100
        elif available_power > TESLA_MIN_WATTS:
            # Only wake the Highland if we have enough surplus to start charging
            logger.info('Surplus > %dW. Waking Tesla for SOC check...', TESLA_MIN_WATTS)
            tesla.wake_up()
            time.sleep(5) # Allow infotainment to boot
            tesla_soc = tesla.get_battery_level() or 100
        else:
            logger.info('Tesla is %s and no surplus available. Skipping SOC poll.', tesla_state)
            tesla_soc = 100 
    # End if tesla_connected

    # 3. Determine SOC (Integrating your Disconnected = 100% logic)
    # Rivian SOC
    rivian_soc = 100
    if rivian_connected:
        rivian_soc = rivian.get_battery_level() or 100

    logger.info('Rivian SOC: %d%%  Tesla SOC: %d%%', rivian_soc, tesla_soc)

    # 4. Calculate real-time consumption
    current_power = 0.0
    if rivian_connected and rivian.is_charging():
        current_power += rivian.get_current_schedule_amp() * VOLTS
    if tesla_connected and tesla.is_charging():
        current_power += tesla.get_current_schedule_amp() * VOLTS


    solar_for_ev = current_power + available_power
    logger.info('Current EV Draw: %.0fW | Total Solar for EVs: %.0fW', current_power, solar_for_ev)

    # --- INTEGRATED LOGIC PIECE ---
    
    # Rule 4: must stop immediately if both vehicles are adequately charged and no surplus
    both_over = rivian_soc > SOC_THRESHOLD and tesla_soc > SOC_THRESHOLD
    must_stop = both_over and solar_for_ev <= 0

    # Skip insignificant power fluctuations unless a forced stop is required
    # (available_power is the current grid export/import)
    if not must_stop and abs(available_power) < CHANGE_THRESHOLD_WATTS:
        logger.info('Power change %.0fW below threshold, skipping update', available_power)
        return

    # --- END INTEGRATED LOGIC PIECE ---


    # 5. Allocate and Apply
    rivian_w, tesla_w = allocate_solar_watts(
        solar_for_ev, rivian_soc, tesla_soc, rivian_connected, tesla_connected
    )

    if rivian_connected:
        apply_charging(rivian, rivian_w, 2, 'Rivian')
    if tesla_connected:
        apply_charging(tesla, tesla_w, 1, 'Tesla')

    logger.info('Automation cycle complete')


