import logging
import json
import asyncio
import aiohttp
import time
from tesla_fleet_api import TeslaFleetApi
from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5
    AMPS_MAX = 48
    
    def __init__(self, config_file="/app/config.json", session_file="/sessions/tesla-session.json"):
        # Reverted to your exact paths as requested
        self.config_file = config_file
        self.session_file = session_file
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
        """Persist tokens to disk to maintain the single-use OAuth chain."""
        try:
            session_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "vehicle_id": self.vehicle_id,
                "updated_at": int(time.time())
            }
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=4)
            logger.info("Tesla tokens updated and saved to disk.")
        except Exception as e:
            logger.error(f"Failed to save Tesla session: {e}")

    def _get_parsed_key(self):
        try:
            with open(self.private_key_path, "rb") as key_file:
                return serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Key parsing failed: {e}")
            return None

    def _run_async(self, coro_func, *args, **kwargs):
        """Bridge between sync main loop and async library."""
        async def wrapper():
            async with aiohttp.ClientSession() as session:
                if not self.access_token:
                    if not await self._refresh_tokens_async(session): return None

                parsed_key = self._get_parsed_key()
                if not parsed_key: return None

                api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                api.private_key = parsed_key
                vehicle = VehicleSigned(api, self.vehicle_id) if self.vehicle_id else None

                try:
                    return await coro_func(api, vehicle, *args, **kwargs)
                except Exception as e:
                    if "401" in str(e).lower() or "unauthorized" in str(e).lower():
                        if await self._refresh_tokens_async(session):
                            api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                            api.private_key = parsed_key
                            vehicle = VehicleSigned(api, self.vehicle_id)
                            return await coro_func(api, vehicle, *args, **kwargs)
                    
                    # THE PROPER FIX: Catch the offline error and return None silently.
                    # Do not 'raise' it to crash the script.
                    if "offline" in str(e).lower():
                        logger.debug("Tesla is asleep/offline.")
                        return None
                        
                    logger.error(f"Tesla Request Failed: {e}")
                    return None
        return asyncio.run(wrapper())

    async def _refresh_tokens_async(self, session):
        url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        try:
            async with session.post(url, data=data) as resp:
                if resp.status != 200: return False
                tokens = await resp.json()
                self.access_token = tokens["access_token"]
                self.refresh_token = tokens["refresh_token"]
                self.save_session()
                return True
        except Exception:
            return False

    # --- Commands (Synchronous Public Methods) ---

    def is_charger_connected(self):
        """
        Fail-Open logic. If get_vehicle_data returns None, the car is asleep. 
        We assume it is connected in the lab so the automation doesn't skip it.
        """
        data = self.get_vehicle_data()
        if not data:
            return True
            
        # Standard check to see if the cable is physically disconnected
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state != 'Disconnected'

    def wake_up(self):
        """Wakes the vehicle safely, skipping the delay if already awake."""
        # 1. Check if the telemetry computer is already online
        if self.get_vehicle_data():
            logger.info("Tesla is already awake. Skipping wake delay.")
            return True

        # 2. If asleep, send the command and wait for Highland stabilization
        logger.info("Sending Tesla wake command...")
        result = self._run_async(lambda _, v: v.wake_up())
        
        logger.info("Waiting 20 seconds for Highland telemetry to stabilize...")
        time.sleep(20)
        
        return result

    def get_vehicle_data(self):
        return self._run_async(lambda _, v: v.vehicle_data())

    def get_battery_level(self):
        """Returns 0 if the car is asleep/offline."""
        data = self.get_vehicle_data()
        if not data:
            return 0 
        return data.get('response', {}).get('charge_state', {}).get('battery_level', 0)

    def is_charging(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state == 'Charging'

    def charge_start(self):
        return self._run_async(lambda _, v: v.charge_start())

    def charge_stop(self):
        return self._run_async(lambda _, v: v.charge_stop())

    def set_charging_amps(self, amps):
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        return self._run_async(lambda _, v: v.set_charging_amps(amps))

    def get_current_schedule_amp(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', {}).get('charge_state', {}).get('charge_amps', 0)

    # --- Compatibility Aliases ---
    def set_schedule_amps(self, amps): return self.set_charging_amps(amps)
    def set_schedule_off(self): return self.charge_stop()
    def set_schedule_default(self): return self.set_schedule_amps(self.AMPS_MAX)