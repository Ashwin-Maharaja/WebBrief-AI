# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage (including JavaScript-rendered single page applications) and generates concise, high-value structured summaries for both short and long-form content across multiple summarization modes.

## Version
**v0.8 — Dynamic Scraping Fallback**

## Features in v0.8
- **Dynamic JavaScript Rendering Fallback**:
  - Two-stage hybrid extraction pipeline: attempts standard static extraction (`requests` + `BeautifulSoup`) first for maximum speed.
  - Automatically invokes headless browser rendering (`Playwright` + Chromium) when static extraction yields inadequate content (e.g., client-side React, Vue, Angular, or SPA shells).
  - Configurable timeouts (`STATIC_TIMEOUT = 15s`, `BROWSER_TIMEOUT = 15s`) and DOM readiness polling.
  - Efficient resource aborts: blocks images, media, web fonts, and stylesheets during browser rendering to minimize bandwidth and latency.
  - Clean lifecycle management: ensures browser contexts and instances are always closed and released.
- **Enterprise-Grade SSRF & Security Defense**:
  - Validates URLs and strictly blocks internal and private network destinations (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`).
  - Blocks `localhost`, `.local`, `.internal`, `.lan`, and cloud metadata endpoints (`169.254.169.254`).
  - DNS resolution validation: verifies that domain names resolve exclusively to public, routable IP addresses.
  - Redirect validation: verifies every intermediate hop and the final destination against SSRF rules.
  - Request filtering in browser: enforces SSRF and scheme checks on subresources and frame navigations.
- **Multiple Summarization Modes (from v0.7)**:
  - ⚡ **Quick Summary** *(Default)*: Highly concise overview focusing strictly on core highlights.
  - 📖 **Detailed Summary**: In-depth explanation with background context, nuances, and comprehensive detail.
  - 📋 **Bullet-Point Summary**: Highly scannable, cleanly structured key takeaways.
  - ⚙️ **Technical Summary**: Strictly preserves technical terminology, specifications, and architecture.
- **Chunking & Map-Reduce Pipeline (from v0.6)**: Paragraph-aware chunking and hierarchical intermediate reduction for long-form content.
- **Structured 5-Section Architecture (from v0.5)**: Consistent format across all modes (Title, Overview, Key Points, Important Details, Conclusion).
- **BYOK (Bring Your Own Key) Support**: Optional custom OpenAI API key field directly in the UI.
- **Security by Design**: Zero exposure of environment variables or API keys to frontend assets or public logs.

## Deployment & Serverless Considerations

- **Headless Browser Binaries**: Standard serverless functions (e.g. AWS Lambda, Vercel Serverless) enforce tight package size limits (~50MB compressed / 250MB uncompressed). Full Chromium binaries can exceed these limits unless deployed using container images or specialized packages (e.g. `@sparticuz/chromium`).
- **Recommended Environments**: Containerized environments (Docker on Render, Railway, Fly.io, Google Cloud Run, AWS ECS) or traditional Linux/Windows VPS provide native support for Playwright browser binaries.
- **Graceful Degradation**: If Playwright or browser binaries are not installed in the target host environment, WebBrief AI automatically degrades gracefully: static scraping remains fully operational, and informative diagnostic errors are returned if dynamic rendering is required.

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes, mode routing, and request coordination
├── chunker.py          # Paragraph-aware chunking and boundary management
├── scraper.py          # Dual static/dynamic scraper, SSRF protection, and Playwright fallback
├── summarizer.py       # Multi-mode prompt engine, Map-Reduce pipeline, and schema validation
├── static/
│   └── style.css       # Responsive styling, mode cards, section styling, animations
├── templates/
│   ├── index.html      # Primary interactive web interface with mode selector
│   └── result.html     # Structured summary view template with mode badging
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore configuration
├── requirements.txt    # Lean dependencies including Playwright
└── README.md           # Documentation
```

## Getting Started

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Dependencies: Flask, requests, beautifulsoup4, openai, python-dotenv, playwright)*

### 2. Install Browser Binaries (for Dynamic Rendering Fallback)
```bash
playwright install chromium
```

### 3. Configure Environment
Copy `.env.example` to `.env` and add your OpenAI API key:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```

### 4. Run the Application
```bash
python app.py
```
Visit `http://127.0.0.1:5000` in your web browser.
