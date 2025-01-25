import requests
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class PowerFlow:
    pv: float  # PV production in watts
    grid: float  # Grid power flow in watts (positive = import, negative = export)
    load: float  # Load consumption in watts

class SolarEdgeAPI:
    BASE_URL = "https://monitoringapi.solaredge.com"
    
    def __init__(self, credentials_file):
        """Initialize with credentials file path"""
        with open(credentials_file) as f:
            credentials = json.load(f)
            
        self.api_key = credentials['solaredge']['api_key']
        self.site_id = credentials['solaredge']['site_id']
        
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

    def get_current_power_flow(self):
        """Get current power flow including PV, Grid and Load"""
        endpoint = f"/site/{self.site_id}/currentPowerFlow"
        url = f"{self.BASE_URL}{endpoint}"
        
        params = {
            "api_key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            power_flow = data['siteCurrentPowerFlow']
            
            # Extract values from power flow data
            pv = power_flow.get('PV', {}).get('currentPower', 0)
            grid = power_flow.get('GRID', {}).get('currentPower', 0)
            load = power_flow.get('LOAD', {}).get('currentPower', 0)
            
            # Grid power is positive when importing, negative when exporting
            # So we need to flip the sign to match common convention
            grid = -grid
            
            return PowerFlow(pv=pv, grid=grid, load=load)
            
        except requests.exceptions.RequestException as e:
            print(f"Error getting current power flow: {e}")
            return None

def print_current_energy_production(api_key, site_id):
    """Print current energy production from SolarEdge inverter"""
    api = SolarEdgeAPI(api_key, site_id)
    power = api.get_current_power()
    
    if power is not None:
        print(f"Current power production: {power} W")
    else:
        print("Could not retrieve current power production")
