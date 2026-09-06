from flask import Flask, render_template, request
from scraper import scrape_article
from summarizer import summarize_text

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/summarize", methods=["POST"])
def summarize():
    url = request.form["url"]

    if not url:
        return "Please enter a webpage URL."

    article_text = scrape_article(url)

    if not article_text:
        return "Could not extract content from this webpage."

    summary = summarize_text(article_text)

    return render_template("result.html", summary=summary)


if __name__ == "__main__":
    app.run(debug=True)