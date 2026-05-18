import requests
from bs4 import BeautifulSoup

def scrape_url(url: str) -> str:
    """
    Fetches the content of a web page and returns the text.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
            
        text = soup.get_text(separator=' ', strip=True)
        return text[:10000]  # Limit to 10k characters to fit in context
    except Exception as e:
        return f"Error scraping URL: {str(e)}"
