from flask import Flask, render_template, request, jsonify
from scraper import scrape_article, ScraperError
from summarizer import summarize_text, SummarizerError

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/summarize", methods=["POST"])
def summarize():
    # Support both AJAX JSON payloads and standard HTML form submissions
    if request.is_json:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        api_key = data.get("api_key", "").strip() or None
        is_ajax = True
    else:
        url = request.form.get("url", "").strip()
        api_key = request.form.get("api_key", "").strip() or None
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Validation: prevent empty submissions
    if not url:
        error_msg = "Please enter a webpage URL."
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=""), 400

    try:
        # Step 1: Scrape article text from webpage
        article_text = scrape_article(url)

        # Step 2: Generate AI summary
        summary = summarize_text(article_text, api_key=api_key)

        if is_ajax:
            return jsonify({
                "success": True,
                "summary": summary,
                "url": url
            })

        return render_template("index.html", summary=summary, url=url)

    except ScraperError as e:
        error_msg = str(e)
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url), 400

    except SummarizerError as e:
        error_msg = str(e)
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url), 500

    except Exception as e:
        error_msg = f"An unexpected error occurred: {str(e)}"
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url), 500


if __name__ == "__main__":
    app.run(debug=True)
