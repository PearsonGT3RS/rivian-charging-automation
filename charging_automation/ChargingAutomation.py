import logging
import math
from datetime import datetime
from enum import Enum
from RivianAPI import RivianAPI
from TeslaAPI import TeslaAPI
from SolarEdgeAPI import SolarEdgeAPI
import json

logger = logging.getLogger(__name__)

class AutomationMode(Enum):
    OFF = 0  # charging automation off
    DEFAULT = 1  # using excess solar during the day, charging at full speed at night (up to a limit)
    SOLAR_ONLY = 2  # only using excess solar, not charging at night


def is_night_time():
    current_hour = datetime.now().hour
    return current_hour < 10 or current_hour >= 18


def calculate_delta_amp(grid_consumption, vehicle_type):
    """Calculate amp adjustment based on grid consumption and vehicle type
    Args:
        grid_consumption: Current grid power flow (positive = importing)
        vehicle_type: Either 'tesla' or 'rivian' to determine amp increment
    Returns:
        Suggested amp adjustment (negative to reduce consumption)
    """
    # Tesla supports 1A increments, Rivian requires 2A
    increment = 1 if vehicle_type == 'tesla' else 2
    
    # Calculate base amp change needed (Watt / 240V)
    base_amps = grid_consumption / 240
    
    # Round to nearest increment in the direction that reduces grid consumption
    # For positive grid (importing), we want to round up the negative adjustment
    # For negative grid (exporting), we want to round down the negative adjustment
    if grid_consumption > 0:
        delta_amp = -math.ceil(abs(base_amps) / increment) * increment
    else:
        delta_amp = -math.floor(abs(base_amps) / increment) * increment
        
    return delta_amp


def is_delta_amp_too_small(delta_amp):
    return -3 < delta_amp < 3


def get_automation_mode(hubitat):
    # If not using Hubitat, hardcode the automation mode
    if not hubitat:
        return AutomationMode.SOLAR_ONLY

    automation_on = hubitat.is_automation_on()
    night_charging = hubitat.is_night_charging_on()
    if not automation_on:
        return AutomationMode.OFF
    return AutomationMode.DEFAULT if night_charging else AutomationMode.SOLAR_ONLY


def get_night_charging_limit(hubitat):
    # If not using Hubitat, hardcode 50%
    if not hubitat:
        return 50

    limit = hubitat.get_night_charging_limit()
    return limit


def allocate_power(vehicles, available_power):
    """Allocate available power to vehicles based on state of charge"""
    # Sort vehicles by state of charge (lowest first)
    sorted_vehicles = sorted(vehicles, key=lambda v: v.get_battery_level())
    
    total_allocated = 0
    allocations = []
    
    for vehicle in sorted_vehicles:
        if not vehicle.is_charger_connected():
            allocations.append(0)
            continue
            
        # Calculate fair share based on remaining vehicles
        remaining_power = available_power - total_allocated
        remaining_vehicles = len(sorted_vehicles) - len(allocations)
        fair_share = remaining_power / remaining_vehicles
        
        # Calculate max possible for this vehicle
        max_possible = min(
            fair_share * 2,  # Bias toward lower SOC vehicles
            vehicle.AMPS_MAX * 240,  # Convert to watts
            remaining_power
        )
        
        allocation = max(0, max_possible)
        allocations.append(allocation)
        total_allocated += allocation
    
    return allocations

def run_charging_automation():
    logger.info('Running charging automation cycle...')

    #hubitat = HubitatAPI('hubitat-config.json')
    # If not using Hubitat replace with the line below
    hubitat = None

    logger.info('Reading config from Hubitat...')
    mode = get_automation_mode(hubitat)

    logger.info('Automation mode: {}'.format(mode))

    # Check automation is ON
    if mode == AutomationMode.OFF:
        logger.info('Automation is OFF')
        return

    # Initialize vehicle APIs
    rivian = RivianAPI('credentials.json', 'rivian-session.json')
    tesla = TeslaAPI('credentials.json', 'tesla-session.json')
    solaredge = SolarEdgeAPI('config.json')
    
    vehicles = [rivian, tesla]

    # Check if any chargers are plugged in
    if not any(v.is_charger_connected() for v in vehicles):
        logger.info('No chargers plugged in')
        for vehicle in vehicles:
            vehicle.set_schedule_off()
        if hubitat:
            hubitat.set_info_message('Charging: not plugged in', 0, 0)
        return

    # Check night time
    if is_night_time():
        if mode == AutomationMode.SOLAR_ONLY:
            logger.info('Mode == Solar-only: Disabling charging at night')
            rivian.set_schedule_off()
            current_amp = 0
            if hubitat:
                hubitat.set_info_message('Charging: disabled (night off)', 0, 0)
        if mode == AutomationMode.DEFAULT:
            # In default mode, charge to a certain % at night
            charging_limit = get_night_charging_limit(hubitat)
            ev_battery_level = rivian.get_battery_level()
            if ev_battery_level < charging_limit:
                logger.info('Mode == Default: Charging to {}% at night (now at {}%)'.format(
                    charging_limit, round(ev_battery_level)))
                rivian.set_schedule_default()
                if hubitat:
                    hubitat.set_info_message('Charging: enabled (night)', RivianAPI.AMPS_MAX, 0)
                #short-circuit if already charging
                return
            else:
                logger.info('Mode == Default: Charged to {}% at night (already at {}%)'.format(
                    charging_limit, round(ev_battery_level)))
                rivian.set_schedule_off()
                current_amp = 0
                if hubitat:
                    hubitat.set_info_message('Charging: disabled (night full)', 0, 0)
        return

    # Read production data from SolarEdge
    power_flow = solaredge.get_current_power_flow()
    if power_flow is None:
        logger.error('Failed to get power flow data')
        return
        
    # Calculate available power (negative grid means excess power)
    available_power = -power_flow.grid
    
    # Get current charging power
    current_power = sum(
        v.get_current_schedule_amp() * 240 if v.is_charging() else 0 
        for v in vehicles
    )
    
    # Calculate power adjustment needed
    power_delta = available_power - current_power
    logger.info(f'Available power: {available_power}W ; Current power: {current_power}W ; Delta: {power_delta}W')

    if abs(power_delta) < 720:  # 3A * 240V = 720W threshold
        logger.info('Small or no change. Ignoring')
        return

    # Allocate power to vehicles
    power_allocations = allocate_power(vehicles, available_power)
    
    # Apply allocations with vehicle-specific increments
    total_amps = 0
    for vehicle, allocation in zip(vehicles, power_allocations):
        vehicle_type = 'tesla' if isinstance(vehicle, TeslaAPI) else 'rivian'
        base_amps = allocation / 240
        amps = calculate_delta_amp(-base_amps * 240, vehicle_type)  # Convert back to grid consumption style
        amps = max(vehicle.AMPS_MIN, min(amps, vehicle.AMPS_MAX))  # Clamp to vehicle limits
        if amps == 0:
            vehicle.set_schedule_off()
        else:
            vehicle.set_schedule_amps(amps)
        total_amps += amps
    
    # Update Hubitat display
    if hubitat:
        if total_amps == 0:
            hubitat.set_info_message('Charging: disabled', 0, power_flow.grid)
        else:
            hubitat.set_info_message(
                f'Charging: {len([v for v in vehicles if v.is_charging()])} vehicles',
                total_amps,
                power_flow.grid
            )

    logger.info('Automation cycle complete')
