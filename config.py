import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
    RIOT_API_KEY = os.getenv('RIOT_API_KEY')
    DEFAULT_REGION = os.getenv('DEFAULT_REGION', 'euw1')
    
    # URLs API Riot
    RIOT_BASE_URL = "https://{region}.api.riotgames.com"
    RIOT_AMERICAS_URL = "https://americas.api.riotgames.com"
    RIOT_EUROPE_URL = "https://europe.api.riotgames.com"
    RIOT_ASIA_URL = "https://asia.api.riotgames.com"
    
    # Mapping des régions
    REGION_MAPPING = {
        'euw1': 'europe',
        'eun1': 'europe',
        'na1': 'americas',
        'br1': 'americas',
        'la1': 'americas',
        'la2': 'americas',
        'kr': 'asia',
        'jp1': 'asia',
        'oc1': 'americas',
        'tr1': 'europe',
        'ru': 'europe'
    }
