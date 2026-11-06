# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage and generates concise, high-value structured summaries for both short and long-form content across multiple summarization modes.

## Version
**v0.7 — Multiple Summarization Modes**

## Features in v0.7
- **Multiple Summarization Modes**:
  - ⚡ **Quick Summary** *(Default)*: Highly concise overview focusing strictly on core highlights and essential conclusions.
  - 📖 **Detailed Summary**: In-depth explanation with background context, nuances, mechanisms, and comprehensive detail.
  - 📋 **Bullet-Point Summary**: Highly scannable, cleanly structured key takeaways avoiding long prose paragraphs.
  - ⚙️ **Technical Summary**: Strictly preserves technical terminology, specifications, architecture, constraints, and relationships.
- **Shared Summarization Engine**: Uses a single unified pipeline with configurable, mode-specific prompt directives rather than fragmented engines.
- **Full Map-Reduce Compatibility**: All modes seamlessly integrate with the v0.6 chunking and map-reduce pipeline for long-form webpages.
- **Intuitive Web UI**: Modern segmented mode selector cards with visual feedback, automatic state persistence, and responsive mobile/desktop layouts.
- **Structured 5-Section Architecture**: Every mode consistently outputs:
  - **Article / Website Title**
  - **Overview**: High-level contextual overview
  - **Key Points**: Core takeaways
  - **Important Details**: Supporting facts, metrics, and details
  - **Conclusion**: Concluding takeaway or outcome
- **Visual Badging**: Displays the active mode badge (e.g. `⚡ Quick Summary`, `⚙️ Technical Summary`) alongside execution stats (`⚡ Direct Analysis` / `📑 Map-Reduce`).
- **BYOK (Bring Your Own Key) Support**: Optional custom OpenAI API key field directly in the UI.
- **Security by Design**: Zero exposure of environment variables or API keys to frontend assets or public logs.

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes, mode routing, and request coordination
├── chunker.py          # Paragraph-aware chunking and boundary management
├── scraper.py          # Webpage fetching, title extraction, and text cleaning
├── summarizer.py       # Multi-mode prompt engine, Map-Reduce pipeline, and schema validation
├── static/
│   └── style.css       # Responsive styling, mode cards, section styling, animations
├── templates/
│   ├── index.html      # Primary interactive web interface with mode selector
│   └── result.html     # Structured summary view template with mode badging
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
