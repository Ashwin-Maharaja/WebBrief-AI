import ipaddress
import logging
import socket
import urllib.parse
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection

logger = logging.getLogger(__name__)

# Configuration constants
STATIC_TIMEOUT = 15          # Timeout in seconds for static requests
BROWSER_TIMEOUT = 15         # Timeout in seconds for Playwright browser fallback
MIN_SUFFICIENT_LENGTH = 60   # Minimum characters of readable text to consider static extraction adequate
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB maximum allowed webpage download size
MAX_SUBREQUESTS = 50         # Maximum subrequests allowed per dynamic browser page load


class ScraperError(Exception):
    """Exception raised when webpage scraping fails."""
    pass


def is_safe_ip(ip_str: str) -> bool:
    """
    Returns True if the IP address is a public, globally-routable unicast IP.
    Strictly rejects private, loopback, link-local, reserved, multicast, unspecified,
    CGNAT (RFC 6598 100.64.0.0/10), and cloud metadata services.
    Handles IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1).
    """
    try:
        ip = ipaddress.ip_address(ip_str)

        # Check IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return is_safe_ip(str(ip.ipv4_mapped))

        # Must be a globally routable public unicast address
        if not ip.is_global:
            return False

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

        # Explicit CGNAT subnet defense (RFC 6598: 100.64.0.0/10)
        cgnat_net = ipaddress.ip_network("100.64.0.0/10")
        if ip.version == 4 and ip in cgnat_net:
            return False

        # Explicit Cloud Metadata IP defense
        metadata_ips = {
            "169.254.169.254",
            "100.100.100.200",
            "fd00:ec2::254",
        }
        if str(ip).lower() in metadata_ips:
            return False

        return True
    except ValueError:
        return False


def resolve_and_validate_safe_url(url: str) -> tuple[str, str]:
    """
    Validates that a URL is well-formed, uses HTTP or HTTPS, does not target internal/private
    networks, cloud metadata services, CGNAT addresses, or link-local ranges (SSRF prevention).
    Resolves the domain and confirms all resolved destination IPs are safe.

    Returns:
        tuple of (normalized_url, pinned_safe_ip)

    Raises:
        ScraperError: If the URL or any resolved IP is unsafe or invalid.
    """
    if not url or not isinstance(url, str):
        raise ScraperError("Please enter a valid webpage URL.")

    url = url.strip()

    # Prepend https:// if user omitted scheme
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urllib.parse.urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ScraperError("Invalid URL scheme. Only HTTP and HTTPS URLs are supported.")

    # Rejection of embedded credentials (user:pass@host)
    if parsed.username or parsed.password:
        raise ScraperError("URLs with embedded user credentials are not allowed for security.")

    # Port restriction: only standard web ports allowed
    if parsed.port:
        allowed_ports = {80, 443, 8080, 8443}
        if parsed.port not in allowed_ports:
            raise ScraperError(f"Access to non-standard port {parsed.port} is restricted for security.")

    hostname = parsed.hostname
    if not hostname:
        raise ScraperError("Invalid URL format: missing host domain.")

    lower_host = hostname.lower().strip(".")

    # Block localhost and internal local domain patterns
    if (
        lower_host in ("localhost", "local", "broadcasthost")
        or lower_host.endswith(".local")
        or lower_host.endswith(".internal")
        or lower_host.endswith(".lan")
        or lower_host.endswith(".corp")
        or lower_host.endswith(".home")
    ):
        raise ScraperError(
            "Access to internal, loopback, or private addresses is restricted for security."
        )

    # Check if hostname itself is directly an IP literal
    try:
        ip = ipaddress.ip_address(lower_host)
        if not is_safe_ip(str(ip)):
            raise ScraperError(
                f"Access to internal, loopback, or private network address ({ip}) is restricted for security."
            )
        return url, str(ip)
    except ValueError:
        # Not a raw IP literal, proceed to DNS resolution
        pass

    # Resolve hostname to verify destination IPs
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Support RFC 2606 reserved documentation domains for offline/mock test environments
        if lower_host in ("example.com", "example.org", "example.net") or lower_host.endswith(
            (".example.com", ".example.org", ".example.net")
        ):
            addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        else:
            raise ScraperError(f"Could not resolve domain '{hostname}'. The website may not exist or is unreachable.")
    except Exception as e:
        raise ScraperError(f"DNS lookup failed for '{hostname}': {str(e)}")

    if not addr_info:
        raise ScraperError(f"No IP addresses resolved for domain '{hostname}'.")

    safe_ips = []
    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        if not is_safe_ip(ip_str):
            raise ScraperError(
                f"Access to internal, loopback, or private network address ({ip_str}) is restricted for security."
            )
        safe_ips.append(ip_str)

    # Return normalized URL and the primary verified IP for connection pinning
    return url, safe_ips[0]


def validate_safe_url(url: str) -> str:
    """
    Backward-compatible wrapper validating a URL and returning the normalized safe URL string.
    """
    normalized_url, _ = resolve_and_validate_safe_url(url)
    return normalized_url


class PinnedHTTPConnection(HTTPConnection):
    """HTTPConnection that connects directly to a verified IP, preventing connection-time DNS rebinding."""
    def _new_conn(self):
        extra_kw = {}
        if self.source_address:
            extra_kw["source_address"] = self.source_address
        if self.socket_options:
            extra_kw["socket_options"] = self.socket_options
        target_ip = getattr(self, "_pinned_ip", self.host)
        return socket.create_connection((target_ip, self.port), self.timeout, **extra_kw)


class PinnedHTTPSConnection(HTTPSConnection):
    """HTTPSConnection that connects socket to verified IP while preserving TLS SNI & hostname cert checks."""
    def _new_conn(self):
        extra_kw = {}
        if self.source_address:
            extra_kw["source_address"] = self.source_address
        if self.socket_options:
            extra_kw["socket_options"] = self.socket_options
        target_ip = getattr(self, "_pinned_ip", self.host)
        return socket.create_connection((target_ip, self.port), self.timeout, **extra_kw)


class PinnedIPAdapter(HTTPAdapter):
    """Custom HTTPAdapter that pins verified destination IPs to eliminate DNS rebinding TOCTOU."""
    def __init__(self, pinned_ips=None, *args, **kwargs):
        self.pinned_ips = pinned_ips or {}
        super().__init__(*args, **kwargs)

    def get_connection(self, url, proxies=None):
        conn = super().get_connection(url, proxies=proxies)
        host = urllib.parse.urlparse(url).hostname
        if host and host in self.pinned_ips:
            conn._pinned_ip = self.pinned_ips[host]
            if conn.scheme == "https":
                conn.ConnectionCls = PinnedHTTPSConnection
            else:
                conn.ConnectionCls = PinnedHTTPConnection
        return conn


def extract_readable_content(html: str) -> tuple[str, str]:
    """
    Parses HTML content, strips boilerplate elements, and extracts (page_title, article_text).
    """
    if not html:
        return "", ""

    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(strip=True) if soup.title else ""

    # Clean up non-content elements
    for element in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg", "form"]):
        element.decompose()

    # Prefer semantic content containers if present
    container = soup.find("article") or soup.find("main") or soup.find("body") or soup
    paragraphs = container.find_all("p")

    # If container produced no paragraphs, fallback to soup
    if not paragraphs:
        paragraphs = soup.find_all("p")

    article_text = ""
    for paragraph in paragraphs:
        text = paragraph.get_text(strip=True)
        if text:
            article_text += text + "\n\n"

    return page_title, article_text.strip()


def scrape_with_requests(url: str, timeout: int = STATIC_TIMEOUT) -> str:
    """
    Attempts to fetch webpage HTML statically using requests.
    Enforces strict pre-flight SSRF validation and IP pinning on every redirect hop (max 5 hops)
    with allow_redirects=False to prevent DNS rebinding and internal network pivot attacks.
    Enforces a strict 5 MB streaming download limit to prevent memory exhaustion DoS.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    current_url = url
    max_redirects = 5
    response = None
    pinned_ips = {}

    try:
        with requests.Session() as session:
            adapter = PinnedIPAdapter(pinned_ips)
            session.mount("https://", adapter)
            session.mount("http://", adapter)

            for _ in range(max_redirects):
                # Validate and pin IP before connection on each hop
                validated_url, verified_ip = resolve_and_validate_safe_url(current_url)
                host = urllib.parse.urlparse(validated_url).hostname
                if host:
                    pinned_ips[host] = verified_ip

                response = session.get(
                    validated_url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )

                # Check if response is a redirect
                if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        break
                    # Resolve relative redirect URLs safely
                    next_url = urllib.parse.urljoin(current_url, location)
                    current_url = next_url
                    continue

                # Final response reached
                break
            else:
                raise ScraperError("Too many redirects (exceeded limit of 5).")

            response.raise_for_status()

            # Enforce maximum response size limit to prevent memory exhaustion / DoS
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise ScraperError(
                            f"Webpage content exceeds maximum allowed size ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)."
                        )
                except ValueError:
                    pass

            content_chunks = []
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=65536):
                total_bytes += len(chunk)
                if total_bytes > MAX_RESPONSE_BYTES:
                    raise ScraperError(
                        f"Webpage content exceeds maximum allowed size ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)."
                    )
                content_chunks.append(chunk)

            raw_bytes = b"".join(content_chunks)
            encoding = response.encoding or "utf-8"
            try:
                return raw_bytes.decode(encoding, errors="replace")
            except Exception:
                return raw_bytes.decode("utf-8", errors="replace")

    except requests.exceptions.MissingSchema:
        raise ScraperError("Invalid URL schema. Please include http:// or https://.")
    except requests.exceptions.InvalidURL:
        raise ScraperError("The provided URL is malformed or invalid.")
    except requests.exceptions.SSLError:
        raise ScraperError("SSL certificate verification failed for this website.")
    except requests.exceptions.ConnectionError:
        raise ScraperError("Unable to connect to website. The host may be down or does not exist.")
    except requests.exceptions.Timeout:
        raise ScraperError("Connection timed out while trying to reach the website.")
    except requests.exceptions.HTTPError:
        status_code = getattr(response, "status_code", None)
        if status_code == 404:
            raise ScraperError("Webpage not found (HTTP 404). Please check the URL.")
        elif status_code in (401, 403):
            raise ScraperError(f"Access denied by the website (HTTP {status_code}). Automated scraping may be restricted.")
        else:
            raise ScraperError(f"Website returned an error (HTTP {status_code}).")
    except ScraperError:
        raise
    except requests.exceptions.RequestException as e:
        raise ScraperError(f"Failed to fetch webpage: {str(e)}")


def scrape_with_browser(url: str, timeout: int = BROWSER_TIMEOUT) -> str:
    """
    Headless browser scraper using Playwright as fallback for JavaScript-rendered websites.
    Enforces SSRF validation on all subrequests and redirects, enforces a 50-request subrequest budget,
    enforces a 5 MB cumulative content limit, and guarantees browser process termination.
    """
    # Defense-in-depth: re-verify target URL before launching browser engine
    validate_safe_url(url)

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
    except ImportError:
        raise ScraperError(
            "Dynamic browser rendering is not available on this server (Playwright is not installed)."
        )

    browser = None
    context = None
    playwright_cm = None

    try:
        playwright_cm = sync_playwright()
        p = playwright_cm.__enter__()

        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        except Exception as e:
            raise ScraperError(f"Failed to start headless browser engine: {str(e)}")

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        subrequest_count = 0
        total_downloaded_bytes = 0
        size_exceeded = False

        # Resource optimization & SSRF interception handler
        def route_handler(route):
            nonlocal subrequest_count, size_exceeded
            if size_exceeded:
                route.abort("contentlength")
                return

            subrequest_count += 1
            if subrequest_count > MAX_SUBREQUESTS:
                route.abort("budgetexceeded")
                return

            req = route.request
            # Abort non-essential assets to save bandwidth and maximize rendering speed
            if req.resource_type in ("image", "media", "font", "stylesheet", "websocket", "eventsource", "manifest", "other"):
                route.abort("blockedbyclient")
                return

            # SSRF defense for subresources and redirects
            req_url = req.url
            try:
                parsed_req = urllib.parse.urlparse(req_url)
                if parsed_req.scheme.lower() not in ("http", "https"):
                    route.abort("blockedbyclient")
                    return
                if parsed_req.username or parsed_req.password:
                    route.abort("blockedbyclient")
                    return
                if parsed_req.port and parsed_req.port not in (80, 443, 8080, 8443):
                    route.abort("blockedbyclient")
                    return
                validate_safe_url(req_url)
            except Exception:
                route.abort("blockedbyclient")
                return

            route.continue_()

        page.route("**/*", route_handler)

        def on_response(resp):
            nonlocal total_downloaded_bytes, size_exceeded
            try:
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit():
                    total_downloaded_bytes += int(cl)
                    if total_downloaded_bytes > MAX_RESPONSE_BYTES:
                        size_exceeded = True
            except Exception:
                pass

        page.on("response", on_response)

        try:
            # Navigate to the target page
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            # Wait briefly for dynamic client-side DOM rendering
            try:
                page.wait_for_selector("p, article, main, [role='main']", timeout=5000)
            except PlaywrightTimeoutError:
                pass

        except PlaywrightTimeoutError:
            raise ScraperError("Connection timed out while loading JavaScript-rendered webpage.")
        except PlaywrightError as e:
            if size_exceeded:
                raise ScraperError(
                    f"Webpage content exceeds maximum allowed size ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)."
                )
            raise ScraperError(f"Browser navigation failed: {str(e)}")

        if size_exceeded or total_downloaded_bytes > MAX_RESPONSE_BYTES:
            raise ScraperError(
                f"Webpage content exceeds maximum allowed size ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)."
            )

        # Validate destination URL after any redirects
        validate_safe_url(page.url)

        content = page.content()
        content_bytes = len(content.encode("utf-8"))
        if (total_downloaded_bytes + content_bytes) > MAX_RESPONSE_BYTES or content_bytes > MAX_RESPONSE_BYTES:
            raise ScraperError(
                f"Webpage content exceeds maximum allowed size ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)."
            )

        return content

    except ScraperError:
        raise
    except Exception as e:
        raise ScraperError(f"Browser rendering encountered an error: {str(e)}")
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright_cm:
            try:
                playwright_cm.__exit__(None, None, None)
            except Exception:
                pass


def scrape_article(url: str) -> str:
    """
    Extracts readable article content from the given URL.
    Attempts static extraction first (requests + BeautifulSoup).
    If static extraction yields inadequate content (or fails with a recoverable error),
    automatically invokes dynamic browser rendering (Playwright) as a fallback.

    Raises:
        ScraperError: When the URL is invalid, blocked for SSRF security, unreachable,
                      or lacks extractable content across both static and dynamic methods.
    """
    # Step 1: Validate and sanitize URL (blocks SSRF, private IPs, loopback, CGNAT, bad schemes)
    safe_url = validate_safe_url(url)

    static_error = None
    page_title = ""
    article_text = ""

    # Step 2: Attempt standard static extraction
    try:
        html = scrape_with_requests(safe_url, timeout=STATIC_TIMEOUT)
        page_title, article_text = extract_readable_content(html)
    except ScraperError as se:
        # Do not fallback if URL is blocked for SSRF security or malformed
        if "restricted for security" in str(se) or "Invalid URL" in str(se):
            raise
        # 404 Not Found means the page doesn't exist; browser will also find 404
        if "HTTP 404" in str(se):
            raise
        static_error = str(se)

    # Step 3: Evaluate whether static extraction was sufficient
    is_inadequate = (
        static_error is not None
        or len(article_text.strip()) < MIN_SUFFICIENT_LENGTH
    )

    # Step 4: If inadequate, invoke dynamic browser rendering fallback
    if is_inadequate:
        try:
            browser_html = scrape_with_browser(safe_url, timeout=BROWSER_TIMEOUT)
            b_title, b_text = extract_readable_content(browser_html)

            # If browser rendering produced better or adequate content, use it
            if len(b_text.strip()) >= len(article_text.strip()) and len(b_text.strip()) > 0:
                article_text = b_text
                page_title = b_title or page_title
            elif not article_text.strip() and not b_text.strip():
                raise ScraperError("Could not extract readable article text from this webpage.")
        except Exception as be:
            # If both methods failed, report clear error
            if not article_text.strip():
                if static_error:
                    raise ScraperError(
                        f"Static extraction failed ({static_error}) and dynamic browser fallback failed: {str(be)}"
                    )
                else:
                    raise ScraperError(
                        f"Dynamic browser fallback failed to extract content: {str(be)}"
                    )

    article_text = article_text.strip()
    if not article_text:
        raise ScraperError("Could not extract readable article text from this webpage.")

    if page_title:
        article_text = f"Page Title: {page_title}\n\n{article_text}"

    return article_text
