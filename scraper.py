import requests
from bs4 import BeautifulSoup

url = "https://en.wikipedia.org/wiki/Artificial_intelligence"

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

with open("article.txt", "w", encoding="utf-8") as file:
    file.write(article_text)

print("Article saved to article.txt")
