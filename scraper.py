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

paragraphs = soup.find_all("p")

print("Paragraphs found:", len(paragraphs))

for paragraph in paragraphs:
    text = paragraph.get_text(strip=True)

    if text:
        print(text)
