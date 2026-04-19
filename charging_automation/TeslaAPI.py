import logging
import json
import asyncio
import aiohttp
from tesla_fleet_api import TeslaFleetApi
from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned

# Cryptography imports for the formal handshake
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5
    AMPS_MAX = 48
    
    def __init__(self, config_file=None, session_file=None):
        # --- PATH CONFIGURATION ---
        # Switch these back to absolute /app/ paths before building your Docker image
        self.config_file = "/app/config.json"
        self.session_file = "/sessions/tesla-session.json"
        self.private_key_path = "/app/private-key.pem"
        
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.vehicle_id = None 
        
        self._load_config()
        self.load_session()
        
    def _load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                config_data = json.load(f)
                self.client_id = config_data.get("tesla_client_id")
                self.client_secret = config_data.get("tesla_client_secret")
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")

    def load_session(self):
        try:
            with open(self.session_file) as f:
                session = json.load(f)
                self.access_token = session.get('access_token')
                self.refresh_token = session.get('refresh_token')
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
            logger.debug("Tesla tokens updated and saved.")

    def _get_parsed_key(self):
        """Returns the formal EllipticCurvePrivateKey object required by the library"""
        try:
            with open(self.private_key_path, "rb") as key_file:
                return serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"CRITICAL: Key parsing failed: {e}")
            return None

    async def charge_start(self):
        """Starts the charging session (Signed Command)"""
        try:
            # self.vehicle is your VehicleSigned instance
            result = await self.vehicle.charge_start()
            logger.info(f"Charge Start command sent: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to start charging: {e}")
            return None

    async def charge_stop(self):
        """Stops the charging session (Signed Command)"""
        try:
            result = await self.vehicle.charge_stop()
            logger.info(f"Charge Stop command sent: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to stop charging: {e}")
            return None


    async def _refresh_tokens_async(self, session):
        logger.info("Refreshing Tesla access token...")
        url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        try:
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    logger.error(f"Token refresh failed: {await resp.text()}")
                    return False
                tokens = await resp.json()
                self.access_token = tokens["access_token"]
                self.refresh_token = tokens["refresh_token"]
                self.save_session()
                return True
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

    def _run_async(self, coro_func, *args, **kwargs):
        async def wrapper():
            async with aiohttp.ClientSession() as session:
                # Initial token check
                if not self.access_token:
                    if await self._refresh_tokens_async(session):
                        self.save_session()  # <--- SAVE AFTER INITIAL REFRESH
                    else:
                        logger.error("Initial token refresh failed.")
                        return None

                # 1. Parse Key
                parsed_key = self._get_parsed_key()
                if not parsed_key: return None

                # 2. Init API and Inject Key
                api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                api.private_key = parsed_key

                # 3. Init Vehicle (VehicleSigned is the wrapper now)
                vehicle = None
                if self.vehicle_id:
                    vehicle = VehicleSigned(api, self.vehicle_id)

                try:
                    return await coro_func(api, vehicle, *args, **kwargs)
                except Exception as e:
                    # Catch 401 Unauthorized errors
                    if "401" in str(e).lower() or "unauthorized" in str(e).lower():
                        logger.info("401 detected, attempting token refresh...")
                        if await self._refresh_tokens_async(session):
                            self.save_session()  # <--- SAVE AFTER RETRY REFRESH
                        
                            # Re-init on retry with new tokens
                            api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                            api.private_key = parsed_key
                            vehicle = VehicleSigned(api, self.vehicle_id)
                            return await coro_func(api, vehicle, *args, **kwargs)
                
                    logger.error(f"Tesla Request Failed: {e}")
                    return None
                
        return asyncio.run(wrapper())

    def save_session(self):
        """Persist the new tokens to the TrueNAS mount."""
        try:
            # Match the path logic you hand-edited for /evapp
            session_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "created_at": int(time.time())
            }
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=4)
            logger.info("Tesla session persisted to disk.")
        except Exception as e:
            logger.error(f"Failed to save Tesla session file: {e}")


    # --- Commands ---

    def get_state(self):
        """Checks the vehicle state (online, asleep, offline) without waking it."""
        try:
            # api.vehicle_list() returns cached cloud state
            vehicles = self._run_async(lambda api, _: api.vehicle_list())
            for v in vehicles.get('vehicles', []):
                if v['vin'] == self.vin:
                    return v['state']
            return "unknown"
        except Exception:
            return "offline"

    def get_battery_level(self):
        """Returns battery level or None if the car is asleep."""
        try:
            data = self.get_vehicle_data()
            return data.get('charge_state', {}).get('battery_level')
        except Exception: # Catch VehicleOffline specifically
            return None



# --- Observation Methods (Read) ---

    def is_charger_connected(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        # The Fleet API response is usually nested under a 'response' key
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state != 'Disconnected' and state is not None

    def is_charging(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state == 'Charging'

    def get_battery_level(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', {}).get('charge_state', {}).get('battery_level', 0)

    def get_current_schedule_amp(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', {}).get('charge_state', {}).get('charge_amps', 0)

    def wake_up(self):
        async def _wake(api, vehicle):
            return await vehicle.wake_up()
        return self._run_async(_wake)
    
    def get_vehicle_data(self):
        async def _get(api, vehicle): return await vehicle.vehicle_data()
        return self._run_async(_get)

    def set_schedule_amps(self, amps):
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        async def _set(api, vehicle):
            logger.info(f"Setting Tesla charging to {amps}A...")
            return await vehicle.set_charging_amps(amps)
        return self._run_async(_set)

    def set_schedule_off(self):
        async def _stop(api, vehicle): return await vehicle.charge_stop()
        return self._run_async(_stop)
        
    def set_schedule_default(self):
        """Resets the vehicle to the maximum charging speed (48A)."""
        logger.info(f"Resetting Tesla charging to default maximum ({self.AMPS_MAX}A)...")
        return self.set_schedule_amps(self.AMPS_MAX)