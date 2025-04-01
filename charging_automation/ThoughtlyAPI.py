import logging
import requests
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class ThoughtlyAPI:
    """Client for Thoughtly Contact API"""
    
    BASE_URL = "https://api.thoughtly.com"
    
    def __init__(self, api_token: str, team_id: str):
        self.api_token = api_token
        self.team_id = team_id
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'x-api-token': self.api_token,
            'team_id': self.team_id
        })

    def create_contact(
        self,
        phone_number: str,
        name: str,
        email: Optional[str] = None,
        country_code: Optional[str] = None,
        tags: Optional[List[str]] = None,
        attributes: Optional[Dict] = None
    ) -> Dict:
        """Create a new contact in Thoughtly
        
        Args:
            phone_number: Contact's phone number (required)
            name: Contact's name (required)
            email: Contact's email (optional)
            country_code: Phone country code (optional)
            tags: List of tags for contact (optional)
            attributes: Custom attributes (optional)
            
        Returns:
            Dict containing API response
        """
        url = f"{self.BASE_URL}/contact/create"
        payload = {
            "phone_number": phone_number,
            "name": name,
            "email": email,
            "country_code": country_code,
            "tags": tags or [],
            "attributes": attributes or {}
        }
        
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create Thoughtly contact: {e}")
            raise
