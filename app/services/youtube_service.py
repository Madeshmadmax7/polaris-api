"""
LifeOS – YouTube Service
Handles YouTube video search and duration extraction.
"""

import re
import requests
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qs


def extract_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    if not url:
        return None
    
    # Handle different YouTube URL formats
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)',
        r'youtube\.com\/embed\/([^&\n?#]+)',
        r'youtube\.com\/v\/([^&\n?#]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


def search_youtube_video(query: str, creator: str = None) -> Optional[Dict]:
    """
    Search for a YouTube video using keywords and optional creator name.
    Returns dict with video_id, title, duration_seconds, and url.
    
    Note: This uses YouTube's search without API key (web scraping approach).
    For production, use YouTube Data API v3.
    """
    try:
        # Format search query
        search_terms = query.replace('+', ' ')
        if creator:
            search_terms = f"{search_terms} {creator}"
        
        # For now, return a placeholder that will be populated by frontend
        # In production, implement YouTube Data API v3 integration
        return {
            "video_id": None,
            "title": f"Search: {search_terms}",
            "duration_seconds": 0,
            "url": f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
        }
    except Exception as e:
        print(f"[YouTube Search Error]: {str(e)}")
        return None


def get_video_duration(video_id: str) -> int:
    """
    Get video duration in seconds from YouTube.
    
    Note: This requires YouTube Data API v3 key for production.
    Returns 0 if unable to fetch.
    """
    try:
        # For production, use YouTube Data API v3:
        # API_KEY = os.getenv("YOUTUBE_API_KEY")
        # url = f"https://www.googleapis.com/youtube/v3/videos?part=contentDetails&id={video_id}&key={API_KEY}"
        # response = requests.get(url)
        # data = response.json()
        # duration_str = data['items'][0]['contentDetails']['duration']
        # return parse_iso8601_duration(duration_str)
        
        # For now, return 0 - duration will be set from frontend
        return 0
    except Exception as e:
        print(f"[YouTube Duration Error]: {str(e)}")
        return 0


def parse_iso8601_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration format (e.g., PT15M33S) to seconds."""
    try:
        import re
        
        # Pattern: PT(hours)H(minutes)M(seconds)S
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration_str)
        
        if not match:
            return 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return 0


def format_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration (e.g., '15 min' or '1h 30min')."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"{hours}h {minutes}min"
        return f"{hours}h"


def create_youtube_search_url(course_title: str, creator: str = None) -> str:
    """
    Create a YouTube search URL from course title and optional creator.
    
    Example:
        create_youtube_search_url("Introduction to dynamic programming", "striver")
        Returns: "introduction+dynamic+programming+striver"
    """
    # Convert to lowercase and replace spaces with +
    search_terms = course_title.lower().replace(' ', '+')
    
    if creator:
        creator_formatted = creator.lower().replace(' ', '+')
        search_terms = f"{search_terms}+{creator_formatted}"
    
    return search_terms
