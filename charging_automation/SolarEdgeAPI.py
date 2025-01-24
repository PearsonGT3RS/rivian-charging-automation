import requests
from datetime import datetime, timedelta

class SolarEdgeAPI:
    BASE_URL = "https://monitoringapi.solaredge.com"
    
    def __init__(self, api_key, site_id):
        self.api_key = api_key
        self.site_id = site_id
        
    def get_current_power(self):
        """Get current power production in watts"""
        endpoint = f"/site/{self.site_id}/overview"
        url = f"{self.BASE_URL}{endpoint}"
        
        params = {
            "api_key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            current_power = data['overview']['currentPower']['power']
            return current_power
            
        except requests.exceptions.RequestException as e:
            print(f"Error getting current power: {e}")
            return None

def print_current_energy_production(api_key, site_id):
    """Print current energy production from SolarEdge inverter"""
    api = SolarEdgeAPI(api_key, site_id)
    power = api.get_current_power()
    
    if power is not None:
        print(f"Current power production: {power} W")
    else:
        print("Could not retrieve current power production")
