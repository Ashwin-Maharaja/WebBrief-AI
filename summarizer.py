from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def summarize_text(article_text):
    response = client.responses.create(
        model="gpt-5.6-luna",
        input=f"Summarize the following webpage content concisely:\n\n{article_text}"
    )

    return response.output_text