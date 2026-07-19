# scrapers/reuters.py
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import hashlib
import time

def scrape_reuters_technology():
    url = "https://www.reuters.com/technology/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Selectores actuales de Reuters (pueden cambiar)
        articles = soup.find_all('article')[:12]  # Máximo 12 por ejecución
        
        items = []
        for article in articles:
            title_tag = article.find('h3') or article.find('h2')
            if not title_tag:
                continue
                
            title = title_tag.get_text(strip=True)
            link_tag = title_tag.find('a') or article.find('a')
            link = link_tag['href'] if link_tag else None
            
            if not link:
                continue
                
            if not link.startswith('http'):
                link = f"https://www.reuters.com{link}"
            
            # Descripción (si existe)
            desc_tag = article.find('p')
            description = desc_tag.get_text(strip=True) if desc_tag else ""
            
            item = {
                'title': title,
                'link': link,
                'summary': description,
                'published': datetime.now().isoformat(),
                'source': 'Reuters Technology',
                'hash': hashlib.sha256(link.encode()).hexdigest()
            }
            items.append(item)
            
        print(f"✓ Reuters Technology: {len(items)} artículos scrapeados")
        return items
        
    except Exception as e:
        print(f"❌ Error scraping Reuters: {e}")
        return []
