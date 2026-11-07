import logging
import math
import time
from datetime import datetime
from enum import Enum
from Config import Config
from HomeAssistantAPI import HomeAssistantAPI
from RivianAPI import RivianAPI
from TeslaAPI import TeslaAPI
from SolarEdgeAPI import SolarEdgeAPI, LoadUnavailableError

logger = logging.getLogger(__name__)

SOC_THRESHOLD = 50           # % state of charge threshold for allocation rules
VOLTS = 240                  # nominal charging voltage
CHANGE_THRESHOLD_WATTS = 500 # ignore fluctuations smaller than ~2A Rivian step
RIVIAN_MIN_WATTS = RivianAPI.AMPS_MIN * VOLTS  # 8A * 240V = 1920W
TESLA_MIN_WATTS = TeslaAPI.AMPS_MIN * VOLTS    # 5A * 240V = 1200W
BMS_BUFFER_PCT = 2           # % buffer to prevent waking for minor battery sag

class AutomationMode(Enum):
    OFF = 0          # automation disabled
    DEFAULT = 1      # solar during day, charge to limit at night
    SOLAR_ONLY = 2   # solar only, no night charging


class CycleResult(Enum):
    """Outcome of a single automation cycle, used by main.py to pick a sleep interval."""
    IDLE = 0                # nothing to do; sleep the long idle interval
    NIGHT_ACTIVE = 1        # actively charging at night; poll frequently
    DAYTIME_ACTIVE = 2      # daytime solar cycle; poll at the daytime interval


def is_night_time(config):
    current_hour = datetime.now().hour
    start = config.night_time_start
    end = config.night_time_end
    logger.info('Start Night: %d ; End Night: %d', start, end)
    if start <= end:
        # Same-day window, e.g. 01:00–05:00
        return start <= current_hour < end
    # Wraps midnight, e.g. 21:00–07:00
    return current_hour >= start or current_hour < end


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
      5. Any vehicle at 100% SOC (or presumed 100% while asleep) gets 0% allocation.

    If a vehicle's allocated share is below its hardware minimum, those
    watts are reassigned to the other vehicle to maximize solar use.

    Returns (rivian_watts, tesla_watts).
    """
    total = max(0.0, solar_for_ev)

    if not rivian_connected and not tesla_connected:
        return 0.0, 0.0

    # Single vehicle: all available solar goes to it
    if rivian_connected and not tesla_connected:
        if rivian_soc >= 100:
            return 0.0, 0.0
        if rivian_soc > SOC_THRESHOLD and solar_for_ev <= 0:
            return 0.0, 0.0
        return total, 0.0

    if tesla_connected and not rivian_connected:
        if tesla_soc >= 100:
            return 0.0, 0.0
        if tesla_soc > SOC_THRESHOLD and solar_for_ev <= 0:
            return 0.0, 0.0
        return 0.0, total

    # Both connected — Rule 4: stop if both adequately charged and no surplus
    if rivian_soc > SOC_THRESHOLD and tesla_soc > SOC_THRESHOLD and solar_for_ev <= 0:
        return 0.0, 0.0

    if total == 0:
        return 0.0, 0.0

    # Determine split ratio by SOC
    # Rule 5: A full (or asleep) vehicle gets 0% of the split
    if rivian_soc >= 100 and tesla_soc >= 100:
        return 0.0, 0.0
    elif rivian_soc >= 100:
        rivian_ratio, tesla_ratio = 0.0, 1.0
    elif tesla_soc >= 100:
        rivian_ratio, tesla_ratio = 1.0, 0.0
    elif rivian_soc <= SOC_THRESHOLD and tesla_soc <= SOC_THRESHOLD:
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


def apply_charging(vehicle, target_watts, increment, vehicle_name, current_schedules=None):
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
            vehicle.set_schedule_amps(target_amps, current_schedules=current_schedules) # Rivian Schedule Amps (no wake-up needed)
    else:
        # Already charging, just update the amperage (no hysteresis needed for amp changes)
        if vehicle_name == "Tesla":
            vehicle.set_charging_amps(target_amps) # Signed Amps
        else:
            vehicle.set_schedule_amps(target_amps, current_schedules=current_schedules) # Rivian Schedule Amps (no wake-up needed)


def run_charging_automation():
    logger.info('Running charging automation cycle...')

    hubitat = None  # Replace with HubitatAPI('hubitat-config.json') to enable
    # NEW: Move SOC check AFTER determining if we actually have solar
    # This prevents the Highland from being woken up for no reason
    mode = get_automation_mode(hubitat)
    logger.info('Automation mode: %s', mode)
    if mode == AutomationMode.OFF:
        logger.info('Automation is OFF')
        return CycleResult.IDLE
    
    config = Config('/app/config.json')
    rivian = RivianAPI(config, '/sessions/rivian-session.json')
    tesla = TeslaAPI('/app/config.json', '/sessions/tesla-session.json')

    # Build a load provider from Home Assistant / Emporia Vue sensors.
    # If HA is not configured, load_provider stays None and SolarEdgeAPI
    # falls back to assuming load == pv (grid == 0).
    load_provider = None
    if config.homeassistant_url and config.homeassistant_token and config.emporia_load_entities:
        ha = HomeAssistantAPI(config.homeassistant_url, config.homeassistant_token)

        def load_provider():
            return ha.get_sensor_sum(config.emporia_load_entities)

    rivian_connected = rivian.is_charger_connected()
    tesla_connected = tesla.is_charger_connected()

    logger.info('Rivian connected: %s  Tesla connected: %s', rivian_connected, tesla_connected)

    if not rivian_connected and not tesla_connected:
        logger.info('No chargers plugged in')
        if hubitat:
            hubitat.set_info_message('Charging: not plugged in', 0, 0)
        return CycleResult.IDLE


    # --- NIGHT TIME HANDLING ---
    if is_night_time(config):

        # Keep the SolarEdge refresh token alive without polling live data.
        # v2 invalidates refresh tokens after 14 days of non-use; this fires
        # at most once per KEEPALIVE_INTERVAL_SECONDS (7 days).
        try:
            SolarEdgeAPI('/app/config.json')._maybe_keepalive_refresh()
        except Exception as e:
            logger.warning('SolarEdge keepalive check failed: %s', e)

        # --- NEW: Early exit if night management is disabled ---
        if not getattr(config, 'enable_night_management', True):
            logger.info('Night management is disabled. Ignoring vehicles until morning.')
            return CycleResult.IDLE
        
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
                
                # Fetch the SOC for whichever vehicle is currently looping
                soc = vehicle.get_battery_level() or 0

                # Sleep-aware check: Wake the car if we need to check SOC for night charging
                if name == "Tesla":
                    if soc == 0: 
                        # --- NEW: Smart Cache Check ---
                        cached_soc = vehicle.get_cached_battery_level()
                        
                        if cached_soc >= charging_limit:
                            logger.info('Tesla is asleep. Cached SOC (%d%%) is >= Night Limit (%d%%). Letting it sleep.', cached_soc, charging_limit)
                            soc = cached_soc
                            continue  # Skips the rest of the night loop safely
                            
                        logger.info('Tesla is asleep, but cached SOC is low. Waking for charge...')
                        vehicle.wake_up()
                        soc = vehicle.get_battery_level() or 0
                        
                        if not vehicle.is_charger_connected():
                            logger.info('Tesla woke up but is disconnected. Skipping night charge.')
                            continue

                # --- NEW OPTIMIZATION: State-Aware Night Charging ---
                # Check current state safely
                try:
                    is_currently_charging = vehicle.is_charging()
                except Exception:
                    is_currently_charging = False

                if soc < charging_limit:
                    if not is_currently_charging:
                        logger.info('%s: below limit (%d%% < %d%%) and not charging. Starting night charge.', name, round(soc), charging_limit)
                        if name == "Tesla":
                            vehicle.charge_start() # Use Signed Start
                            vehicle.set_charging_amps(vehicle.AMPS_MAX)
                        else:
                            vehicle.set_schedule_amps(vehicle.AMPS_MAX) # Use Schedule Amps for Rivian
                else:
                    if is_currently_charging:
                        logger.info('%s: at limit (%d%%). Stopping night charge.', name, round(soc))
                        if name == "Tesla":
                            vehicle.charge_stop()
                        else:
                            vehicle.set_schedule_off() # Use Schedule Off for Rivian to avoid wake-up
        
        logger.info('Night-time processing complete.')

        # --- NEW FIX: Evaluate state specifically for the night ---
        # If we are actively charging at night, stay on 5-min loops to prevent overcharging.
        # If resting, safely return False to trigger the 30-min sleep in main.py.
        try:
            actively_charging = (rivian_connected and rivian.is_charging()) or \
                                (tesla_connected and tesla.is_charging())
            return CycleResult.NIGHT_ACTIVE if actively_charging else CycleResult.IDLE
        except Exception:
            return CycleResult.NIGHT_ACTIVE  # Failsafe: stay on the short interval if API fails
            
    # --- END NIGHT TIME HANDLING ---

    # Daytime Solar Logic continues here...
    # Construct the SolarEdge client only in the daytime branch so we don't
    # trigger an overnight token refresh while night management is active.
    solaredge = SolarEdgeAPI('/app/config.json')

    # 1. Check Power Flow before polling vehicle sensors
    try:
        power_flow = solaredge.get_current_power_flow(load_provider=load_provider)
    except LoadUnavailableError:
        logger.error('Emporia load unavailable; aborting cycle for safety')
        return CycleResult.IDLE
    if power_flow is None:
        logger.error('Failed to get power flow data')
        return CycleResult.IDLE

    # available_power: positive = surplus, negative = grid draw
    available_power = -power_flow.grid
    logger.info(
        'Surplus available: %.0fW (pv=%.0fW load=%.0fW grid=%.0fW)',
        available_power, power_flow.pv, power_flow.load, power_flow.grid,
    )

    # Guard against transient SolarEdge reporting gaps: if PV reads 0 during
    # daylight hours, treat it as a bad sample and skip the cycle rather than
    # acting on it (which would stop charging and waste solar).
    current_hour = datetime.now().hour
    if power_flow.pv <= 0 and 8 <= current_hour < 17:
        logger.warning(
            'PV reported %.0fW during daylight hours; skipping cycle to avoid '
            'acting on a transient SolarEdge reporting gap.',
            power_flow.pv,
        )
        return CycleResult.DAYTIME_ACTIVE

    # Tesla SOC (Integrating Sleep-Aware + Disconnected = 100% logic)
    tesla_soc = 100 # Default to "Full" to prevent accidental draw
    if tesla_connected:
        tesla_soc = tesla.get_battery_level() # returns 0 if asleep
        
        if tesla_soc == 0:
            cached_soc = tesla.get_cached_battery_level()
            cached_limit = tesla.get_cached_vehicle_limit()
            
            # --- FIX: BMS Hysteresis Buffer (2%) ---
            # Teslas will reject a charge if they are within a few percent of their limit.
            if cached_soc > 0 and cached_soc >= (cached_limit - BMS_BUFFER_PCT):
                logger.info('Tesla is asleep. Cached SOC (%d%%) is near vehicle limit (%d%%). Letting it sleep.', cached_soc, cached_limit)
                tesla_soc = 100
            elif available_power > TESLA_MIN_WATTS:
                logger.info('Surplus > %dW. Waking Tesla for solar charge...', TESLA_MIN_WATTS)
                tesla.wake_up()
                tesla_soc = tesla.get_battery_level() or 100
                
                # Check limit again with buffer after waking
                if tesla_soc >= (tesla.get_vehicle_limit() - BMS_BUFFER_PCT):
                    logger.info('Tesla woke up but is near its internal limit. Treating as full.')
                    tesla_soc = 100
                elif not tesla.is_charger_connected():
                    logger.info('Tesla woke up but is disconnected. Re-allocating solar.')
                    tesla_connected = False
                    tesla_soc = 100
            else:
                logger.info('Tesla is asleep and no surplus available. Let it sleep.')
                tesla_soc = 100 
        else:
            # --- FIX: Car is awake. Respect the internal screen limit with buffer! ---
            tesla_limit = tesla.get_vehicle_limit()
            if tesla_soc >= (tesla_limit - BMS_BUFFER_PCT):
                logger.info('Tesla is near its internal screen limit (%d%%). Treating as full.', tesla_limit)
                tesla_soc = 100
            else:
                tesla_soc = tesla_soc
    # End if tesla_connected

    # 3. Determine SOC (Integrating your Disconnected = 100% logic)
    # Rivian SOC
    rivian_soc = 100
    if rivian_connected:
        rivian_soc = rivian.get_battery_level() or 100
        
        # --- FIX: GHOST RIVIAN & VEHICLE LIMIT DETECTION ---
        # If the API caches 'connected', but it refuses to draw power 
        # (unplugged or hit internal vehicle limit), safely bypass it.
        try:
            # We use get_current_schedule_amp() > 0 to know we tried to charge it
            if not rivian.is_charging() and rivian.get_current_schedule_amp() > 0:
                logger.info('Rivian has a schedule but is drawing 0W (Ghost Car). Marking as disconnected.')
                rivian_connected = False
                rivian_soc = 100
        except Exception:
            pass

    logger.info('Rivian SOC: %d%%  Tesla SOC: %d%%', rivian_soc, tesla_soc)

    # 4. Calculate real-time consumption
    # Fetch Rivian schedules once and reuse for both the current-power calc
    # and the subsequent apply_charging() call to avoid a redundant API round-trip.
    rivian_schedules = None
    if rivian_connected:
        try:
            rivian_schedules = rivian.get_current_schedules()
        except Exception as e:
            logger.warning('Failed to fetch Rivian schedules: %s', e)
            rivian_schedules = None

    current_power = 0.0
    if rivian_connected and rivian.is_charging():
        if rivian_schedules:
            current_power += rivian_schedules[0].get('amperage', 0) * VOLTS
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
        return CycleResult.DAYTIME_ACTIVE

    # --- END INTEGRATED LOGIC PIECE ---


    # 5. Allocate and Apply
    rivian_w, tesla_w = allocate_solar_watts(
        solar_for_ev, rivian_soc, tesla_soc, rivian_connected, tesla_connected
    )

    if rivian_connected:
        apply_charging(rivian, rivian_w, 2, 'Rivian', current_schedules=rivian_schedules)
    if tesla_connected:
        apply_charging(tesla, tesla_w, 1, 'Tesla')

    logger.info('Automation cycle complete')

    # Daytime cycles always return DAYTIME_ACTIVE so main.py polls solar
    # production at the daytime interval.
    return CycleResult.DAYTIME_ACTIVE
