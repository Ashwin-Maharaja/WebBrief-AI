# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage and generates concise, high-value structured summaries for both short and long-form content.

## Version
**v0.6 — Chunking and Map-Reduce Summarization**

## Features in v0.6
- **Long-Form Summarization Pipeline**: Supports arbitrarily long webpages and documents via paragraph-aware chunking and Map-Reduce.
- **Paragraph & Sentence-Aware Chunking**: Content is divided at paragraph and sentence boundaries rather than arbitrary byte/character breaks, preserving context and coherence.
- **Contextual Chunk Overlap**: Sensible chunk overlap ensures continuity and prevents concept fragmentation across section boundaries.
- **Adaptive Execution**:
  - **Direct Analysis**: Short webpages (< chunk threshold) execute in a single direct pass, eliminating extra API calls and latency.
  - **Map Phase**: Independent, factual extraction for each content section.
  - **Reduce Phase**: Unified synthesis that deduplicates overlapping context and outputs the final structured schema.
- **Partial Failure Resilience**: Tolerates transient individual chunk errors without aborting the entire process if other sections succeed.
- **Structured 5-Section Output**: Consistently outputs:
  - **Article / Website Title**
  - **Overview**: High-level contextual overview
  - **Key Points**: Core takeaways
  - **Important Details**: Supporting facts, metrics, and details
  - **Conclusion**: Concluding takeaway or outcome
- **Visual Execution Badging**: UI displays whether an article was processed via `⚡ Direct Analysis` or `📑 Map-Reduce (N sections)`.
- **BYOK (Bring Your Own Key) Support**: Optional custom OpenAI API key field directly in the UI.
- **Security by Design**: Zero exposure of environment variables or API keys to frontend assets or public logs.

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes and request coordination
├── chunker.py          # Paragraph-aware chunking and boundary management
├── scraper.py          # Webpage fetching, title extraction, and text cleaning
├── summarizer.py       # Map-reduce pipeline, OpenAI communication, and schema validation
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
