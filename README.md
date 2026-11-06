# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage and generates a concise, high-value structured summary.

## Version
**v0.5 — Structured Summarization Output**

## Features in v0.5
- **Structured Summary Architecture**: Summaries are consistently organized into distinct, high-value sections:
  - **Article / Website Title**
  - **Overview**: High-level contextual overview
  - **Key Points**: Core takeaways
  - **Important Details**: Supporting facts, metrics, and details
  - **Conclusion**: Concluding takeaway or outcome
- **Strict Factuality Prompting**: Explicit guardrails against hallucinations; summaries strictly reflect source content.
- **Graceful Section Handling**: Short or limited content articles seamlessly omit empty optional sections without breaking the UI.
- **Modular Architecture**: Complete separation of prompt construction, model communication, validation, and presentation.
- **Flask Web Interface**: Clean, responsive layout with mobile and desktop typography hierarchy.
- **Interactive Feedback**: Instant loading indicator, error alerts for malformed URLs/unreachable sites, and one-click copy-to-clipboard.
- **BYOK (Bring Your Own Key) Support**: Optional custom OpenAI API key field directly in the UI (falls back to server environment variable if left blank).
- **Security by Design**: Zero exposure of environment variables or API keys to frontend assets or public logs.

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes and request coordination
├── scraper.py          # Webpage fetching, title extraction, and text cleaning
├── summarizer.py       # OpenAI structured summarization pipeline & schema validation
├── static/
│   └── style.css       # Responsive styling, structured section cards, animations
├── templates/
│   ├── index.html      # Primary interactive web interface
│   └── result.html     # Structured summary view template
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore configuration
├── requirements.txt    # Lean dependencies
└── README.md           # Documentation
```

## Getting Started

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Core dependencies: Flask, requests, beautifulsoup4, openai, python-dotenv)*

### 2. Configure Environment
Copy `.env.example` to `.env` and add your OpenAI API key:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```

### 3. Run the Application
```bash
python app.py
```
Visit `http://127.0.0.1:5000` in your web browser.
