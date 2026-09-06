import requests
from bs4 import BeautifulSoup


def scrape_article(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers)

    print("Status:", response.status_code)
    print("Length:", len(response.text))

    soup = BeautifulSoup(response.text, "html.parser")

    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    paragraphs = soup.find_all("p")

    article_text = ""

    for paragraph in paragraphs:
        text = paragraph.get_text(strip=True)

        if text:
            article_text += text + "\n\n"

    return article_text