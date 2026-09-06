from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=api_key)

print("OpenAI client created successfully")

with open("article.txt", "r", encoding="utf-8") as file:
    article_text = file.read()

print("Article loaded:", len(article_text), "characters")

response = client.responses.create(
    model="gpt-5.6-luna",
    instructions="You are a helpful webpage summarization assistant.",
    input=f"Summarize the following webpage content concisely:\n\n{article_text}"
)

summary = response.output_text

print("\nSUMMARY:")
print(summary)

with open("summary.txt", "w", encoding="utf-8") as file:
    file.write(summary)

print("Summary saved to summary.txt")