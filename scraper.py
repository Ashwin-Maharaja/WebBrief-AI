import urllib.parse
import requests
from bs4 import BeautifulSoup


class ScraperError(Exception):
    """Exception raised when webpage scraping fails."""
    pass


def scrape_article(url):
    """
    Extracts readable article content from the given URL.
    
    Raises:
        ScraperError: When the URL is invalid, unreachable, or lacks extractable content.
    """
    if not url or not isinstance(url, str):
        raise ScraperError("Please enter a valid webpage URL.")

    url = url.strip()

    # Prepend https:// if user omitted scheme
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ScraperError("Invalid URL format. Please enter a valid URL (e.g., https://example.com).")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.MissingSchema:
        raise ScraperError("Invalid URL schema. Please include http:// or https://.")
    except requests.exceptions.InvalidURL:
        raise ScraperError("The provided URL is malformed or invalid.")
    except requests.exceptions.SSLError:
        raise ScraperError("SSL certificate verification failed for this website.")
    except requests.exceptions.ConnectionError:
        raise ScraperError("Unable to connect to website. The host may be down or does not exist.")
    except requests.exceptions.Timeout:
        raise ScraperError("Connection timed out while trying to reach the website.")
    except requests.exceptions.HTTPError as e:
        status_code = response.status_code if response is not None else None
        if status_code == 404:
            raise ScraperError("Webpage not found (HTTP 404). Please check the URL.")
        elif status_code in (401, 403):
            raise ScraperError(f"Access denied by the website (HTTP {status_code}). Automated scraping may be restricted.")
        else:
            raise ScraperError(f"Website returned an error (HTTP {status_code}).")
    except requests.exceptions.RequestException as e:
        raise ScraperError(f"Failed to fetch webpage: {str(e)}")
    except Exception as e:
        raise ScraperError(f"Error accessing website: {str(e)}")

    soup = BeautifulSoup(response.text, "html.parser")

    # Capture page title before element cleanup
    page_title = soup.title.get_text(strip=True) if soup.title else ""

    # Clean up non-content elements
    for element in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        element.decompose()

    paragraphs = soup.find_all("p")
    article_text = ""

    for paragraph in paragraphs:
        text = paragraph.get_text(strip=True)
        if text:
            article_text += text + "\n\n"

    article_text = article_text.strip()

    if not article_text:
        raise ScraperError("Could not extract readable article text from this webpage.")

    if page_title:
        article_text = f"Page Title: {page_title}\n\n{article_text}"

    return article_text
