import logging
import json
import asyncio
from tesla_fleet_api import TeslaFleetApi

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5   # Minimum charging amps
    AMPS_MAX = 48  # Maximum charging amps
    
    def __init__(self, config_file, session_file):
        # Override passed arguments to use absolute Docker container paths
        self.config_file = "/app/config.json"
        self.session_file = "/app/sessions/tesla-session.json"
        
        # Fleet API Configuration
        self.client_id = "a4c25c2a-ffc7-42d7-bd0f-4dcb0dda0156"
        self.domain = "www.jenniferpearson.com"
        self.private_key_path = "/app/private-key.pem"  # Required for Highland command signing
        
        self.access_token = None
        self.refresh_token = None
        self.vehicle_id = None 
        
        self.load_session()
        
    def load_session(self):
        try:
            with open(self.session_file) as f:
                session = json.load(f)
                self.access_token = session.get('access_token')
                self.refresh_token = session.get('refresh_token')
                
                # CRITICAL: Fleet API requires the VIN, not the legacy integer ID
                self.vehicle_id = session.get('vehicle_id') 
                
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("No valid session file found")
            
    def save_session(self):
        with open(self.session_file, 'w') as f:
            json.dump({
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'vehicle_id': self.vehicle_id
            }, f)
            logger.debug("Tesla tokens updated and saved to session.")

    def _run_async(self, coro_func, *args, **kwargs):
        """
        Synchronous wrapper to execute async Fleet API calls.
        """
        async def wrapper():
            async with TeslaFleetApi(
                client_id=self.client_id,
                access_token=self.access_token,
                refresh_token=self.refresh_token
            ) as api:
                
                vehicle = None
                if self.vehicle_id:
                    vehicle = api.vehicle(self.vehicle_id)
                    await vehicle.signed.set_key(self.private_key_path)

                try:
                    result = await coro_func(api, vehicle, *args, **kwargs)
                except Exception as e:
                    logger.error("Tesla Fleet API Request Failed: %s", e)
                    result = None

                if api.refresh_token and api.refresh_token != self.refresh_token:
                    logger.info("Tesla tokens rotated. Saving new tokens.")
                    self.refresh_token = api.refresh_token
                    self.access_token = api.access_token
                    self.save_session()
                    
                return result
                
        return asyncio.run(wrapper())

    # --- Read Methods (Observation) ---

    def get_vehicle_data(self):
        if not self.vehicle_id:
            logger.warning("No VIN set in session file.")
            return None
            
        async def _get_data(api, vehicle):
            return await vehicle.vehicle_data()
            
        return self._run_async(_get_data)

    def is_charger_connected(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        state = data.get('response', data).get('charge_state', {}).get('charging_state')
        return state != 'Disconnected' and state is not None
        
    def is_charging(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        state = data.get('response', data).get('charge_state', {}).get('charging_state')
        return state == 'Charging'
        
    def get_battery_level(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', data).get('charge_state', {}).get('battery_level', 0)
        
    def get_current_schedule_amp(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', data).get('charge_state', {}).get('charge_amps', 0)
        
    def wake_up(self):
        if not self.vehicle_id:
            return False
            
        async def _wake(api, vehicle):
            return await vehicle.wake_up()
            
        return self._run_async(_wake)

    # --- Write Methods (Highland Command Signed) ---

    def set_schedule_off(self):
        if not self.vehicle_id:
            return False
            
        async def _stop_charge(api, vehicle):
            logger.info("Sending signed charge_stop command...")
            return await vehicle.signed.charge_stop()
            
        return self._run_async(_stop_charge)
        
    def set_schedule_amps(self, amps):
        if not self.vehicle_id:
            return False
            
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        
        async def _set_amps(api, vehicle):
            logger.info("Sending signed set_charging_amps command (%dA)...", amps)
            return await vehicle.signed.set_charging_amps(amps)
            
        return self._run_async(_set_amps)
        
    def set_schedule_default(self):
        return self.set_schedule_amps(self.AMPS_MAX)