import logging
import json
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5  # Minimum charging amps
    AMPS_MAX = 48  # Maximum charging amps
    BASE_URL = "https://owner-api.teslamotors.com"
    
    def __init__(self, config_file, session_file):
        self.config_file = config_file
        self.session_file = session_file
        self.access_token = None
        self.vehicle_id = None
        self.load_session()
        
    def load_session(self):
        try:
            with open(self.session_file) as f:
                session = json.load(f)
                self.access_token = session.get('access_token')
                self.vehicle_id = session.get('vehicle_id')
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("No valid session file found")
            
    def save_session(self):
        with open(self.session_file, 'w') as f:
            json.dump({
                'access_token': self.access_token,
                'vehicle_id': self.vehicle_id
            }, f)
            
    def _api_request(self, method, endpoint, data=None):
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = requests.request(method, url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None
            
    def get_vehicle_data(self):
        if not self.vehicle_id:
            return None
        endpoint = f"/api/1/vehicles/{self.vehicle_id}/vehicle_data"
        return self._api_request('GET', endpoint)
        
    def wake_up(self):
        if not self.vehicle_id:
            return False
        endpoint = f"/api/1/vehicles/{self.vehicle_id}/wake_up"
        return self._api_request('POST', endpoint)
        
    def is_charger_connected(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        return data.get('response', {}).get('charge_state', {}).get('charging_state') != 'Disconnected'
        
    def is_charging(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        return data.get('response', {}).get('charge_state', {}).get('charging_state') == 'Charging'
        
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
        
    def set_schedule_off(self):
        if not self.vehicle_id:
            return False
        endpoint = f"/api/1/vehicles/{self.vehicle_id}/command/charge_stop"
        return self._api_request('POST', endpoint)
        
    def set_schedule_amps(self, amps):
        if not self.vehicle_id:
            return False
            
        # Clamp amps to valid range
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        
        endpoint = f"/api/1/vehicles/{self.vehicle_id}/command/set_charging_amps"
        return self._api_request('POST', endpoint, {'amps': amps})
        
    def set_schedule_default(self):
        return self.set_schedule_amps(self.AMPS_MAX)
