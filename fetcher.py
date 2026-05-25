import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import urllib.parse
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fetcher")

# Custom headers to prevent blocks (particularly from Reddit and other sensitive platforms)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 (AI-Digest-Bot/1.0; contact: admin@aidigest.local)"
}

def safe_request(url, headers=HEADERS, timeout=10):
    """Executes a GET request with error handling and returns the response."""
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None

def fetch_hacker_news_ai(date_str, limit=20):
    """Fetches trending AI stories from Hacker News using the Algolia API."""
    logger.info("Fetching Hacker News AI stories...")
    # Get stories from the past 36 hours
    time_limit = int((datetime.utcnow() - timedelta(hours=36)).timestamp())
    url = f"https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>{time_limit}&query=AI&hitsPerPage={limit}"
    
    response = safe_request(url)
    articles = []
    if response:
        try:
            data = response.json()
            for hit in data.get("hits", []):
                title = hit.get("title")
                url_link = hit.get("url")
                # Fallback to HN thread if url is missing (like Ask HN or Show HN)
                if not url_link:
                    url_link = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                
                desc = f"Points: {hit.get('points')} | Comments: {hit.get('num_comments')} | Author: {hit.get('author')}"
                articles.append({
                    "source": "Hacker News",
                    "title": title,
                    "description": desc,
                    "url": url_link,
                    "category": "news"
                })
        except Exception as e:
            logger.error(f"Error parsing Hacker News response: {e}")
    return articles

def fetch_reddit_ai(subreddits=None, limit=10):
    """Fetches new/hot AI developments from key subreddits."""
    logger.info("Fetching Reddit AI topics...")
    if not subreddits:
        subreddits = ["MachineLearning", "singularity", "ArtificialInteligence"]
    articles = []

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
        response = safe_request(url)
        if response:
            try:
                data = response.json()
                for post in data.get("data", {}).get("children", []):
                    post_data = post.get("data", {})
                    # Skip stickied posts
                    if post_data.get("stickied"):
                        continue
                    
                    title = post_data.get("title")
                    permalink = post_data.get("permalink")
                    url_link = f"https://www.reddit.com{permalink}"
                    score = post_data.get("score")
                    comments = post_data.get("num_comments")
                    selftext = post_data.get("selftext", "")
                    
                    # Truncate long descriptions
                    desc = selftext[:200] + "..." if len(selftext) > 200 else selftext
                    if not desc.strip():
                        desc = f"Reddit Post in r/{sub} | Score: {score} | Comments: {comments}"
                    else:
                        desc = f"[{sub}] {desc} | Score: {score} | Comments: {comments}"
                        
                    articles.append({
                        "source": f"Reddit r/{sub}",
                        "title": title,
                        "description": desc,
                        "url": url_link,
                        "category": "news"
                    })
            except Exception as e:
                logger.error(f"Error parsing Reddit r/{sub}: {e}")
    return articles

def fetch_huggingface_papers(limit=15):
    """Fetches daily paper releases from Hugging Face."""
    logger.info("Fetching Hugging Face Daily Papers...")
    url = "https://huggingface.co/api/daily_papers"
    response = safe_request(url)
    articles = []

    if response:
        try:
            data = response.json()
            for paper in data[:limit]:
                paper_obj = paper.get("paper", {})
                title = paper_obj.get("title")
                paper_id = paper_obj.get("id")
                url_link = f"https://huggingface.co/papers/{paper_id}"
                summary = paper_obj.get("summary", "")
                desc = summary[:300] + "..." if len(summary) > 300 else summary
                
                # Format votes
                upvotes = paper.get("publishedAt", "")
                stars = paper_obj.get("upvotes", 0)
                desc = f"{desc} (Upvotes: {stars})"
                
                articles.append({
                    "source": "Hugging Face Daily Papers",
                    "title": title,
                    "description": desc,
                    "url": url_link,
                    "category": "paper"
                })
        except Exception as e:
            logger.error(f"Error parsing Hugging Face Daily Papers: {e}")
    return articles

def fetch_arxiv_ai(limit=15):
    """Fetches recent AI preprints from Arxiv."""
    logger.info("Fetching Arxiv AI preprints...")
    url = f"https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
    response = safe_request(url)
    articles = []
    
    if response:
        try:
            root = ET.fromstring(response.content)
            # Register namespaces to locate entries
            namespaces = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('atom:entry', namespaces):
                title = entry.find('atom:title', namespaces).text.strip().replace('\n', ' ')
                summary = entry.find('atom:summary', namespaces).text.strip().replace('\n', ' ')
                url_link = entry.find('atom:id', namespaces).text.strip()
                
                desc = summary[:300] + "..." if len(summary) > 300 else summary
                
                articles.append({
                    "source": "Arxiv",
                    "title": title,
                    "description": desc,
                    "url": url_link,
                    "category": "paper"
                })
        except Exception as e:
            logger.error(f"Error parsing Arxiv XML response: {e}")
    return articles

def fetch_github_trending(keywords=None, limit=15):
    """Fetches trending AI/LLM GitHub repositories created recently."""
    logger.info("Fetching GitHub Trending AI Repositories...")
    if not keywords:
        keywords = ["ai", "llm", "agent", "machine-learning", "neural"]
    fifteen_days_ago = (datetime.utcnow() - timedelta(days=15)).strftime("%Y-%m-%d")
    kw_str = " OR ".join(keywords)
    query = f"created:>{fifteen_days_ago} ({kw_str})"
    encoded_query = urllib.parse.quote(query)
    url = f"https://api.github.com/search/repositories?q={encoded_query}&sort=stars&order=desc&per_page={limit}"
    
    response = safe_request(url)
    repos = []
    if response:
        try:
            data = response.json()
            for repo in data.get("items", []):
                name = repo.get("full_name")
                desc = repo.get("description") or "No description provided."
                url_link = repo.get("html_url")
                stars = repo.get("stargazers_count")
                forks = repo.get("forks_count")
                lang = repo.get("language") or "Python"
                
                summary = f"Language: {lang} | Stars: {stars} | Forks: {forks} | {desc}"
                repos.append({
                    "source": "GitHub Trending",
                    "title": name,
                    "description": summary,
                    "url": url_link,
                    "category": "repo"
                })
        except Exception as e:
            logger.error(f"Error parsing GitHub Trending repos: {e}")
    return repos

def fetch_product_hunt_ai():
    """Fetches the latest tech launches from Product Hunt RSS."""
    logger.info("Fetching Product Hunt launches...")
    url = "https://www.producthunt.com/feed"
    response = safe_request(url)
    products = []
    
    if response:
        try:
            soup = BeautifulSoup(response.content, features="xml")
            entries = soup.find_all("entry")
            for entry in entries:
                title = entry.find("title").text
                url_link = entry.find("link").get("href")
                content = entry.find("content").text
                # Filter titles or content containing AI keywords to focus on AI launches
                ai_keywords = ["ai", "gpt", "llm", "agent", "copilot", "chatgpt", "deepseek", "claude", "bot", "assistant"]
                text_to_search = (title + " " + content).lower()
                if any(kw in text_to_search for kw in ai_keywords):
                    # Clean up content using BS4 if it's HTML
                    desc_soup = BeautifulSoup(content, "html.parser")
                    desc = desc_soup.get_text()
                    desc = desc[:250] + "..." if len(desc) > 250 else desc
                    
                    products.append({
                        "source": "Product Hunt",
                        "title": title,
                        "description": desc,
                        "url": url_link,
                        "category": "tool"
                    })
        except Exception as e:
            logger.error(f"Error parsing Product Hunt RSS: {e}")
    return products

def fetch_lab_blogs():
    """Fetches blog/news updates from major AI labs using standard feeds."""
    logger.info("Fetching AI Lab Blogs (OpenAI, DeepMind, Anthropic)...")
    feeds = [
        {"name": "OpenAI Blog", "url": "https://openai.com/news/rss/", "category": "news"},
        {"name": "Google DeepMind Blog", "url": "https://deepmind.google/blog/rss.xml", "category": "news"}
    ]
    articles = []
    
    for feed in feeds:
        response = safe_request(feed["url"])
        if response:
            try:
                soup = BeautifulSoup(response.content, features="xml")
                # RSS item is usually <item> or <entry>
                items = soup.find_all("item") or soup.find_all("entry")
                for item in items[:5]: # Take top 5 recent announcements
                    title = item.find("title").text.strip()
                    url_link = (item.find("link").text or item.find("link").get("href") or "").strip()
                    description_el = item.find("description") or item.find("summary")
                    desc = description_el.text.strip() if description_el else "New announcement from lab blog."
                    # Strip HTML
                    desc = BeautifulSoup(desc, "html.parser").get_text()
                    desc = desc[:250] + "..." if len(desc) > 250 else desc
                    
                    articles.append({
                        "source": feed["name"],
                        "title": title,
                        "description": desc,
                        "url": url_link,
                        "category": feed["category"]
                    })
            except Exception as e:
                logger.error(f"Error parsing RSS for {feed['name']}: {e}")
                
    # Anthropic lacks a standard stable RSS feed, let's scrape their news directory frontpage safely
    anthropic_url = "https://www.anthropic.com/news"
    response = safe_request(anthropic_url)
    if response:
        try:
            soup = BeautifulSoup(response.content, "html.parser")
            # Anthropic news links are usually inside articles or post headers
            for post in soup.find_all("a", href=True):
                href = post["href"]
                if "/news/" in href and len(href) > 6:
                    title = post.get_text().strip()
                    if not title or len(title) < 15:
                        continue
                    full_url = f"https://www.anthropic.com{href}" if href.startswith("/") else href
                    articles.append({
                        "source": "Anthropic Blog",
                        "title": title,
                        "description": f"Latest release and research announcement on Anthropic's blog.",
                        "url": full_url,
                        "category": "news"
                    })
        except Exception as e:
            logger.error(f"Error crawling Anthropic blog: {e}")
            
    return articles

def fetch_all_sources(date_str, config=None):
    """Orchestrates data fetching across all targets, compiling items into a flat list."""
    logger.info(f"Triggering global AI news crawl for date {date_str}...")
    if config is None:
        config = {}
    all_items = []

    def _cfg(key):
        return config.get(key, {})

    # 1. Hacker News AI
    hn = _cfg("hacker_news")
    if hn.get("enabled", True):
        try:
            all_items.extend(fetch_hacker_news_ai(date_str, limit=hn.get("limit", 20)))
        except Exception as e:
            logger.error(f"Error fetching HN: {e}")

    # 2. Reddit AI Communities
    rd = _cfg("reddit")
    if rd.get("enabled", True):
        try:
            all_items.extend(fetch_reddit_ai(subreddits=rd.get("subreddits"), limit=rd.get("limit", 10)))
        except Exception as e:
            logger.error(f"Error fetching Reddit: {e}")

    # 3. Hugging Face Daily Papers
    hf = _cfg("huggingface")
    if hf.get("enabled", True):
        try:
            all_items.extend(fetch_huggingface_papers(limit=hf.get("limit", 15)))
        except Exception as e:
            logger.error(f"Error fetching Hugging Face: {e}")

    # 4. Arxiv preprints
    ax = _cfg("arxiv")
    if ax.get("enabled", True):
        try:
            all_items.extend(fetch_arxiv_ai(limit=ax.get("limit", 15)))
        except Exception as e:
            logger.error(f"Error fetching Arxiv: {e}")

    # 5. GitHub Trending AI repos
    gh = _cfg("github")
    if gh.get("enabled", True):
        try:
            all_items.extend(fetch_github_trending(keywords=gh.get("keywords"), limit=gh.get("limit", 15)))
        except Exception as e:
            logger.error(f"Error fetching GitHub: {e}")

    # 6. Product Hunt AI tools
    ph = _cfg("product_hunt")
    if ph.get("enabled", True):
        try:
            all_items.extend(fetch_product_hunt_ai())
        except Exception as e:
            logger.error(f"Error fetching Product Hunt: {e}")

    # 7. AI Lab blogs
    lb = _cfg("lab_blogs")
    if lb.get("enabled", True):
        try:
            all_items.extend(fetch_lab_blogs())
        except Exception as e:
            logger.error(f"Error fetching lab blogs: {e}")
        
    # Deduplicate based on URL
    unique_items = []
    seen_urls = set()
    for item in all_items:
        url = item.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_items.append(item)
            
    logger.info(f"Data crawl completed. Successfully gathered {len(unique_items)} unique AI items.")
    return unique_items
