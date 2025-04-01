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

    def get_agents(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        sort: Optional[str] = None,
        all_interviews: Optional[bool] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None
    ) -> Dict:
        """Get list of agents from Thoughtly
        
        Args:
            search: Search term to filter agents
            status: Filter by status ('ACTIVE' or 'ARCHIVED')
            sort: Sort field ('title_asc', 'title_desc', 'created_asc', 'created_desc')
            all_interviews: Include archived agents
            page: Page number for pagination
            limit: Number of results per page (1-50)
            
        Returns:
            Dict containing API response with agents list
        """
        url = f"{self.BASE_URL}/interview"
        params = {
            'search': search,
            'status': status,
            'sort': sort,
            'all_interviews': all_interviews,
            'page': page,
            'limit': limit
        }
        # Remove None values from params
        params = {k: v for k, v in params.items() if v is not None}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get Thoughtly agents: {e}")
            raise

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
