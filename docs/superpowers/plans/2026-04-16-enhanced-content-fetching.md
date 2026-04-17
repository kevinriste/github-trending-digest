# Enhanced Content Fetching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add specialized content fetchers for PDFs, YouTube, GitHub, and arxiv URLs so HN digest summaries are based on actual content instead of titles alone.

**Architecture:** Extend `fetch_article_content` with a URL-type dispatcher that routes to specialized fetchers before falling through to the existing HTML pipeline. All fetchers return a `FetchedContent` dataclass. PDF fetcher also provides a temp file for direct Gemini upload during summary generation.

**Tech Stack:** markitdown (PDF extraction), youtube-transcript-api (transcripts), GitHub REST API + GraphQL (via requests + gh auth token), google-genai file upload (existing)

**Spec:** `docs/superpowers/specs/2026-04-16-enhanced-content-fetching-design.md`
**Backfill IDs:** `docs/superpowers/specs/backfill-item-ids.txt` (29 items needing regeneration after implementation)

---

### Task 1: Add dependencies and FetchedContent dataclass

**Files:**
- Modify: `pyproject.toml:7-14`
- Modify: `trending_digest.py:1-27` (imports), `trending_digest.py:103` (after constants)

- [ ] **Step 1: Add new dependencies to pyproject.toml**

In `pyproject.toml`, add `markitdown` and `youtube-transcript-api` to the dependencies list:

```toml
dependencies = [
    "beautifulsoup4>=4.12.3",
    "imap-tools>=1.9.0",
    "google-genai>=1.0.0",
    "markitdown>=0.1.0",
    "psycopg[binary]>=3.2.12",
    "requests>=2.32.3",
    "trafilatura>=2.0.0",
    "youtube-transcript-api>=1.0.0",
]
```

- [ ] **Step 2: Run uv sync to install**

Run: `uv sync`
Expected: Dependencies installed without errors.

- [ ] **Step 3: Add new imports to trending_digest.py**

After the existing `from urllib.parse import urlparse` line (line 20), add `parse_qs`:

```python
from urllib.parse import urlparse, parse_qs
```

After the existing `from google import genai` (line 26), add:

```python
from markitdown import MarkItDown
from youtube_transcript_api import YouTubeTranscriptApi
```

Also add to stdlib imports (after `import time` on line 14):

```python
import tempfile
from dataclasses import dataclass
```

- [ ] **Step 4: Add FetchedContent dataclass**

After `HN_ARTICLE_CONTENT_MAX_CHARS` (line 103), add:

```python
PDF_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50MB


@dataclass
class FetchedContent:
    """Result from a content fetcher — text for DB caching, optional file for Gemini upload."""
    text: str
    file_path: str = ""
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml trending_digest.py uv.lock
git commit -m "feat: add dependencies and FetchedContent dataclass for enhanced fetching"
```

---

### Task 2: Add retry helper and GitHub token caching

**Files:**
- Modify: `trending_digest.py` — add after `FetchedContent` dataclass (around line 115)

- [ ] **Step 1: Add retry helper**

```python
def retry_fetch(fn, max_attempts=3, base_delay=1.0):
    """Retry a callable with exponential backoff. Returns the result or re-raises the last exception."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                logging.warning("Retry %d/%d after %.1fs: %s", attempt + 1, max_attempts, delay, exc)
                time.sleep(delay)
    raise last_exc
```

- [ ] **Step 2: Add GitHub token caching**

```python
_github_token: str | None = None


def get_github_token() -> str:
    """Get GitHub API token via gh CLI, cached for the process lifetime."""
    global _github_token
    if _github_token is None:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh auth token failed: {result.stderr.strip()}")
        _github_token = result.stdout.strip()
    return _github_token


def github_api_get(endpoint: str) -> dict:
    """GET from GitHub REST API with auth and retry."""
    token = get_github_token()
    def _do():
        resp = requests.get(
            f"https://api.github.com{endpoint}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 403):
            retry_after = resp.headers.get("Retry-After", "60")
            logging.warning("GitHub rate limited on %s, Retry-After: %s", endpoint, retry_after)
            raise requests.HTTPError(f"Rate limited: {resp.status_code}", response=resp)
        resp.raise_for_status()
        return resp.json()
    return retry_fetch(_do)


def github_graphql(query: str) -> dict:
    """Execute a GitHub GraphQL query with auth and retry."""
    token = get_github_token()
    def _do():
        resp = requests.post(
            "https://api.github.com/graphql",
            headers={"Authorization": f"bearer {token}"},
            json={"query": query},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data
    return retry_fetch(_do)
```

- [ ] **Step 3: Verify imports work**

Run: `uv run python3 -c "from trending_digest import FetchedContent, retry_fetch, get_github_token; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add trending_digest.py
git commit -m "feat: add retry helper and GitHub API utilities"
```

---

### Task 3: PDF fetcher

**Files:**
- Modify: `trending_digest.py` — add after GitHub API utilities

- [ ] **Step 1: Add fetch_pdf_content function**

```python
def fetch_pdf_content(url: str) -> FetchedContent:
    """Download a PDF and extract text via markitdown. Returns text + temp file path."""
    try:
        resp = retry_fetch(lambda: requests.get(url, timeout=60, stream=True))
        content_length = int(resp.headers.get("Content-Length", 0))
        if content_length > PDF_MAX_DOWNLOAD_BYTES:
            logging.warning("PDF too large (%d bytes), skipping: %s", content_length, url)
            return FetchedContent(text="")

        pdf_bytes = resp.content
        if len(pdf_bytes) > PDF_MAX_DOWNLOAD_BYTES:
            logging.warning("PDF too large (%d bytes), skipping: %s", len(pdf_bytes), url)
            return FetchedContent(text="")

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(pdf_bytes)
        tmp.close()

        text = ""
        try:
            md = MarkItDown()
            result = md.convert(tmp.name)
            text = result.text_content or ""
            if len(text) > HN_ARTICLE_CONTENT_MAX_CHARS:
                text = text[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
            logging.info("Extracted %d chars from PDF via markitdown: %s", len(text), url)
        except Exception as exc:
            logging.warning("markitdown extraction failed for %s: %s", url, exc)

        return FetchedContent(text=text, file_path=tmp.name)
    except Exception as exc:
        logging.warning("PDF fetch failed for %s: %s", url, exc)
        return FetchedContent(text="")
```

- [ ] **Step 2: Test with a real PDF**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_pdf_content
result = fetch_pdf_content(\"https://aegis.sourceforge.net/auug97.pdf\")
print(f\"Text length: {len(result.text)}\")
print(f\"File path: {result.file_path}\")
print(f\"First 200 chars: {result.text[:200]}\")
import os; os.unlink(result.file_path)
"'
```
Expected: Non-empty text extracted, temp file path present.

- [ ] **Step 3: Test with arxiv PDF**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_pdf_content
result = fetch_pdf_content(\"https://arxiv.org/pdf/2501.12345\")
print(f\"Text length: {len(result.text)}\")
print(f\"Has file: {bool(result.file_path)}\")
if result.file_path:
    import os; os.unlink(result.file_path)
"'
```
Expected: Text extracted from the arxiv PDF.

- [ ] **Step 4: Commit**

```bash
git add trending_digest.py
git commit -m "feat: add PDF content fetcher with markitdown extraction"
```

---

### Task 4: YouTube fetcher

**Files:**
- Modify: `trending_digest.py` — add after `fetch_pdf_content`

- [ ] **Step 1: Add extract_youtube_id and fetch_youtube_transcript**

```python
def extract_youtube_id(url: str) -> str:
    """Extract video ID from a YouTube URL. Returns empty string if not a video page."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
        return video_id if video_id else ""

    if host in ("youtube.com", "music.youtube.com"):
        path = parsed.path
        # /watch?v=ID
        v = parse_qs(parsed.query).get("v")
        if v:
            return v[0]
        # /shorts/ID, /embed/ID, /v/ID, /live/ID
        for prefix in ("/shorts/", "/embed/", "/v/", "/live/"):
            if path.startswith(prefix):
                segment = path[len(prefix):].split("/")[0].split("?")[0]
                if segment:
                    return segment
        return ""

    return ""


def fetch_youtube_transcript(url: str) -> FetchedContent:
    """Fetch transcript for a YouTube video via youtube-transcript-api."""
    video_id = extract_youtube_id(url)
    if not video_id:
        logging.info("Not a YouTube video URL, skipping transcript: %s", url)
        return FetchedContent(text="")

    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=["en"])
    except Exception:
        try:
            transcript = api.fetch(video_id)
        except Exception as exc:
            logging.warning("YouTube transcript unavailable for %s: %s", url, exc)
            return FetchedContent(text="")

    text = " ".join(entry.text for entry in transcript)
    if len(text) > HN_ARTICLE_CONTENT_MAX_CHARS:
        text = text[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
    logging.info("Fetched YouTube transcript (%d chars) for %s", len(text), url)
    return FetchedContent(text=text)
```

- [ ] **Step 2: Test video ID extraction**

Run:
```bash
uv run python3 -c "
from trending_digest import extract_youtube_id
tests = [
    ('https://www.youtube.com/watch?v=4d_FvgQ1csE', '4d_FvgQ1csE'),
    ('https://www.youtube.com/watch?v=Lw4W9V57SKs&t=5716s', 'Lw4W9V57SKs'),
    ('https://www.youtube.com/shorts/X8qR4aVaI4g', 'X8qR4aVaI4g'),
    ('https://www.youtube.com/premium', ''),
    ('https://www.youtube.com/?app=desktop', ''),
]
for url, expected in tests:
    result = extract_youtube_id(url)
    status = 'PASS' if result == expected else f'FAIL (got {result!r})'
    print(f'{status}: {url}')
"
```
Expected: All PASS.

- [ ] **Step 3: Test transcript fetch**

Run:
```bash
uv run python3 -c "
from trending_digest import fetch_youtube_transcript
result = fetch_youtube_transcript('https://www.youtube.com/watch?v=4d_FvgQ1csE')
print(f'Length: {len(result.text)} chars')
print(f'First 200: {result.text[:200]}')
"
```
Expected: Non-empty transcript text.

- [ ] **Step 4: Test non-video URL gracefully returns empty**

Run:
```bash
uv run python3 -c "
from trending_digest import fetch_youtube_transcript
result = fetch_youtube_transcript('https://www.youtube.com/premium')
print(f'Text: {result.text!r}')
"
```
Expected: `Text: ''`

- [ ] **Step 5: Commit**

```bash
git add trending_digest.py
git commit -m "feat: add YouTube transcript fetcher"
```

---

### Task 5: GitHub fetcher

**Files:**
- Modify: `trending_digest.py` — add after `fetch_youtube_transcript`

- [ ] **Step 1: Add fetch_github_content with all sub-type handlers**

```python
def fetch_github_content(url: str) -> FetchedContent:
    """Fetch content from GitHub URLs via REST API / GraphQL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]

    try:
        # gist.github.com/{user}/{id}
        if host == "gist.github.com" and len(segments) >= 2:
            return _fetch_github_gist(segments[-1])

        # raw.githubusercontent.com
        if host == "raw.githubusercontent.com":
            return _fetch_raw_github(url)

        if host != "github.com":
            return FetchedContent(text="")

        # Need at least owner/repo
        if len(segments) < 2:
            return FetchedContent(text="")

        owner, repo = segments[0], segments[1]

        # github.com/{owner}/{repo}/issues/{n}
        if len(segments) >= 4 and segments[2] == "issues" and segments[3].isdigit():
            return _fetch_github_issue(owner, repo, segments[3])

        # github.com/{owner}/{repo}/pull/{n}
        if len(segments) >= 4 and segments[2] == "pull" and segments[3].isdigit():
            return _fetch_github_pr(owner, repo, segments[3])

        # github.com/{owner}/{repo}/discussions/{n}
        if len(segments) >= 4 and segments[2] == "discussions" and segments[3].isdigit():
            return _fetch_github_discussion(owner, repo, int(segments[3]))

        # github.com/{owner}/{repo}/commit/{sha}
        if len(segments) >= 4 and segments[2] == "commit":
            return _fetch_github_commit(owner, repo, segments[3])

        # github.com/{owner}/{repo}/releases/tag/{tag}
        if len(segments) >= 5 and segments[2] == "releases" and segments[3] == "tag":
            return _fetch_github_release(owner, repo, "/".join(segments[4:]))

        # github.com/{owner}/{repo}/releases (latest)
        if len(segments) == 3 and segments[2] == "releases":
            return _fetch_github_latest_release(owner, repo)

        # github.com/{owner}/{repo}/security/advisories/{id}
        if len(segments) >= 5 and segments[2] == "security" and segments[3] == "advisories":
            return _fetch_github_advisory(owner, repo, segments[4])

        # github.com/{owner}/{repo}/blob/{ref}/{path...}
        if len(segments) >= 5 and segments[2] == "blob":
            ref = segments[3]
            file_path = "/".join(segments[4:])
            if file_path.lower().endswith(".pdf"):
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{file_path}"
                return fetch_pdf_content(raw_url)
            return _fetch_github_file(owner, repo, file_path, ref)

        # github.com/{owner}/{repo}/tree/{ref}/{path...}
        if len(segments) >= 5 and segments[2] == "tree":
            ref = segments[3]
            dir_path = "/".join(segments[4:])
            return _fetch_github_directory_readme(owner, repo, dir_path, ref)

        # github.com/{owner}/{repo} — fetch README
        if len(segments) == 2:
            return _fetch_github_readme(owner, repo)

        # Unrecognized pattern
        return FetchedContent(text="")

    except Exception as exc:
        logging.warning("GitHub fetch failed for %s: %s", url, exc)
        return FetchedContent(text="")


def _fetch_github_readme(owner: str, repo: str) -> FetchedContent:
    import base64
    data = github_api_get(f"/repos/{owner}/{repo}/readme")
    if not data:
        return FetchedContent(text="")
    content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    if len(content) > HN_ARTICLE_CONTENT_MAX_CHARS:
        content = content[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
    logging.info("Fetched GitHub README for %s/%s (%d chars)", owner, repo, len(content))
    return FetchedContent(text=content)


def _fetch_github_issue(owner: str, repo: str, number: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/issues/{number}")
    if not data:
        return FetchedContent(text="")
    text = f"Issue: {data.get('title', '')}\n\n{data.get('body', '') or ''}"
    logging.info("Fetched GitHub issue %s/%s#%s (%d chars)", owner, repo, number, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_pr(owner: str, repo: str, number: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/pulls/{number}")
    if not data:
        return FetchedContent(text="")
    text = f"Pull Request: {data.get('title', '')}\n\n{data.get('body', '') or ''}"
    logging.info("Fetched GitHub PR %s/%s#%s (%d chars)", owner, repo, number, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_discussion(owner: str, repo: str, number: int) -> FetchedContent:
    query = """
    query {
      repository(owner: "%s", name: "%s") {
        discussion(number: %d) {
          title
          body
        }
      }
    }
    """ % (owner, repo, number)
    data = github_graphql(query)
    disc = data.get("data", {}).get("repository", {}).get("discussion")
    if not disc:
        return FetchedContent(text="")
    text = f"Discussion: {disc.get('title', '')}\n\n{disc.get('body', '') or ''}"
    logging.info("Fetched GitHub discussion %s/%s#%d (%d chars)", owner, repo, number, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_commit(owner: str, repo: str, sha: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/commits/{sha}")
    if not data:
        return FetchedContent(text="")
    commit = data.get("commit", {})
    message = commit.get("message", "")
    files = [f.get("filename", "") for f in data.get("files", [])]
    stats = data.get("stats", {})
    text = f"Commit: {message}\n\nFiles changed: {', '.join(files[:20])}\nStats: +{stats.get('additions', 0)} -{stats.get('deletions', 0)}"
    logging.info("Fetched GitHub commit %s/%s@%s (%d chars)", owner, repo, sha[:8], len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_release(owner: str, repo: str, tag: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
    if not data:
        return FetchedContent(text="")
    text = f"Release: {data.get('name', '') or tag}\n\n{data.get('body', '') or ''}"
    logging.info("Fetched GitHub release %s/%s@%s (%d chars)", owner, repo, tag, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_latest_release(owner: str, repo: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/releases/latest")
    if not data:
        return FetchedContent(text="")
    text = f"Release: {data.get('name', '') or data.get('tag_name', '')}\n\n{data.get('body', '') or ''}"
    logging.info("Fetched GitHub latest release for %s/%s (%d chars)", owner, repo, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_advisory(owner: str, repo: str, advisory_id: str) -> FetchedContent:
    data = github_api_get(f"/repos/{owner}/{repo}/security-advisories/{advisory_id}")
    if not data:
        return FetchedContent(text="")
    severity = data.get("severity", "unknown")
    text = f"Security Advisory ({severity}): {data.get('summary', '')}\n\n{data.get('description', '') or ''}"
    logging.info("Fetched GitHub advisory %s/%s/%s (%d chars)", owner, repo, advisory_id, len(text))
    return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])


def _fetch_github_file(owner: str, repo: str, file_path: str, ref: str) -> FetchedContent:
    import base64
    data = github_api_get(f"/repos/{owner}/{repo}/contents/{file_path}?ref={ref}")
    if not data or not isinstance(data, dict):
        return FetchedContent(text="")
    content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    if len(content) > HN_ARTICLE_CONTENT_MAX_CHARS:
        content = content[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
    logging.info("Fetched GitHub file %s/%s/%s (%d chars)", owner, repo, file_path, len(content))
    return FetchedContent(text=content)


def _fetch_github_directory_readme(owner: str, repo: str, dir_path: str, ref: str) -> FetchedContent:
    import base64
    data = github_api_get(f"/repos/{owner}/{repo}/readme/{dir_path}?ref={ref}")
    if data and isinstance(data, dict) and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        if len(content) > HN_ARTICLE_CONTENT_MAX_CHARS:
            content = content[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
        logging.info("Fetched GitHub directory README for %s/%s/%s (%d chars)", owner, repo, dir_path, len(content))
        return FetchedContent(text=content)
    # Fallback: list directory files
    listing = github_api_get(f"/repos/{owner}/{repo}/contents/{dir_path}?ref={ref}")
    if listing and isinstance(listing, list):
        files = [f"{item['name']} ({item['type']}, {item.get('size', 0)}B)" for item in listing[:50]]
        text = f"Directory listing for {dir_path}:\n" + "\n".join(files)
        return FetchedContent(text=text[:HN_ARTICLE_CONTENT_MAX_CHARS])
    return FetchedContent(text="")


def _fetch_raw_github(url: str) -> FetchedContent:
    """Fetch raw.githubusercontent.com content, routing PDFs to the PDF fetcher."""
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return fetch_pdf_content(url)
    try:
        resp = retry_fetch(lambda: requests.get(url, timeout=30))
        text = resp.text
        if len(text) > HN_ARTICLE_CONTENT_MAX_CHARS:
            text = text[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
        logging.info("Fetched raw GitHub content for %s (%d chars)", url, len(text))
        return FetchedContent(text=text)
    except Exception as exc:
        logging.warning("Raw GitHub fetch failed for %s: %s", url, exc)
        return FetchedContent(text="")


def _fetch_github_gist(gist_id: str) -> FetchedContent:
    data = github_api_get(f"/gists/{gist_id}")
    if not data:
        return FetchedContent(text="")
    files = data.get("files", {})
    parts = []
    for name, info in files.items():
        content = info.get("content", "")
        parts.append(f"--- {name} ---\n{content}")
    text = "\n\n".join(parts)
    if len(text) > HN_ARTICLE_CONTENT_MAX_CHARS:
        text = text[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."
    logging.info("Fetched GitHub gist %s (%d chars)", gist_id, len(text))
    return FetchedContent(text=text)
```

- [ ] **Step 2: Test repo README fetch**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_github_content
result = fetch_github_content(\"https://github.com/SethPyle376/hiraeth\")
print(f\"Length: {len(result.text)}\")
print(f\"First 200: {result.text[:200]}\")
"'
```
Expected: README content returned.

- [ ] **Step 3: Test issue fetch**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_github_content
result = fetch_github_content(\"https://github.com/anthropics/claude-code/issues/45756\")
print(f\"Length: {len(result.text)}\")
print(f\"Starts with: {result.text[:50]}\")
"'
```
Expected: Text starting with `Issue:`.

- [ ] **Step 4: Test discussion (GraphQL) fetch**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_github_content
result = fetch_github_content(\"https://github.com/mikf/gallery-dl/discussions/9304\")
print(f\"Length: {len(result.text)}\")
print(f\"Starts with: {result.text[:50]}\")
"'
```
Expected: Text starting with `Discussion:`.

- [ ] **Step 5: Test gist fetch**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_github_content
result = fetch_github_content(\"https://gist.github.com/gritzko/6e81b5391eacb585ae207f5e634db07e\")
print(f\"Length: {len(result.text)}\")
print(f\"First 100: {result.text[:100]}\")
"'
```
Expected: Gist file content.

- [ ] **Step 6: Test org/user profile returns empty**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_github_content
result = fetch_github_content(\"https://github.com/torvalds\")
print(f\"Text: {result.text!r}\")
"'
```
Expected: `Text: ''`

- [ ] **Step 7: Commit**

```bash
git add trending_digest.py
git commit -m "feat: add GitHub content fetcher with REST API and GraphQL support"
```

---

### Task 6: Wire up URL dispatcher in fetch_article_content

**Files:**
- Modify: `trending_digest.py:499-558` — rewrite `fetch_article_content`

- [ ] **Step 1: Rewrite fetch_article_content to dispatch by URL type**

Replace the existing `fetch_article_content` function (currently lines 499-558) with:

```python
def classify_url(url: str) -> str:
    """Classify a URL into a content type for fetcher dispatch."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    if path.endswith(".pdf"):
        return "pdf"
    if "arxiv.org" in host:
        if "/abs/" in parsed.path or "/pdf/" in parsed.path:
            return "pdf"
    if host in ("raw.githubusercontent.com",):
        return "github"
    if host.removeprefix("www.").removeprefix("m.") in ("youtube.com", "youtu.be", "music.youtube.com"):
        return "youtube"
    if host in ("github.com", "gist.github.com"):
        return "github"
    return "html"


def _rewrite_arxiv_to_pdf(url: str) -> str:
    """Convert arxiv abstract URL to PDF URL."""
    return url.replace("/abs/", "/pdf/")


def fetch_article_content(url: str) -> FetchedContent:
    """Fetch article content, dispatching to specialized fetchers by URL type.

    Routes PDFs, YouTube, GitHub, and arxiv URLs to dedicated fetchers.
    Falls back to the existing Puppeteer + trafilatura pipeline for HTML.
    """
    if not url:
        return FetchedContent(text="")

    url_type = classify_url(url)

    if url_type == "pdf":
        if "arxiv.org/abs/" in url:
            url = _rewrite_arxiv_to_pdf(url)
        return fetch_pdf_content(url)

    if url_type == "youtube":
        return fetch_youtube_transcript(url)

    if url_type == "github":
        return fetch_github_content(url)

    # HTML path — existing Puppeteer + trafilatura pipeline
    return _fetch_html_content(url)


def _fetch_html_content(url: str) -> FetchedContent:
    """Fetch HTML article content via Puppeteer + trafilatura (original pipeline)."""
    html_content: str | None = None

    parsed = urlparse(url)
    is_nytimes = parsed.hostname and parsed.hostname.endswith("nytimes.com")
    fetcher_url = NYTIMES_FETCHER_URL if is_nytimes else LOCAL_FETCHER_URL

    try:
        resp = requests.get(fetcher_url, params={"url": url}, timeout=90)
        resp.raise_for_status()
        html_content = resp.text
        logging.info("Fetched article HTML via %s fetcher for %s (%d bytes)",
                     "nytimes" if is_nytimes else "local", url, len(html_content))
    except Exception as exc:
        logging.warning("Local fetcher unavailable for %s: %s — falling back to trafilatura", url, exc)

    if not html_content:
        try:
            html_content = trafilatura.fetch_url(url)
            if html_content:
                logging.info("Fetched article HTML via trafilatura for %s (%d bytes)", url, len(html_content))
        except Exception as exc:
            logging.warning("trafilatura fetch failed for %s: %s", url, exc)

    if not html_content:
        return FetchedContent(text="")

    metadata = trafilatura.bare_extraction(html_content, with_metadata=True)
    body_text = trafilatura.extract(html_content, include_comments=False, favor_recall=True) or ""

    article_title = ""
    if metadata:
        meta_dict = metadata if isinstance(metadata, dict) else metadata.as_dict()
        article_title = meta_dict.get("title") or ""

    if article_title and body_text:
        content = f"{article_title}.\n\n{body_text}"
    elif body_text:
        content = body_text
    else:
        content = article_title

    if len(content) > HN_ARTICLE_CONTENT_MAX_CHARS:
        content = content[:HN_ARTICLE_CONTENT_MAX_CHARS] + "..."

    return FetchedContent(text=content)
```

- [ ] **Step 2: Test URL classification**

Run:
```bash
uv run python3 -c "
from trending_digest import classify_url
tests = [
    ('https://example.com/paper.pdf', 'pdf'),
    ('https://arxiv.org/abs/2501.12345', 'pdf'),
    ('https://arxiv.org/pdf/2501.12345', 'pdf'),
    ('https://www.youtube.com/watch?v=abc', 'youtube'),
    ('https://youtu.be/abc', 'youtube'),
    ('https://github.com/foo/bar', 'github'),
    ('https://gist.github.com/user/abc', 'github'),
    ('https://raw.githubusercontent.com/foo/bar/main/README.md', 'github'),
    ('https://foo.github.io/bar', 'html'),
    ('https://example.com/article', 'html'),
]
for url, expected in tests:
    result = classify_url(url)
    status = 'PASS' if result == expected else f'FAIL (got {result!r})'
    print(f'{status}: {url}')
"
```
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add trending_digest.py
git commit -m "feat: add URL dispatcher to route PDFs, YouTube, GitHub to specialized fetchers"
```

---

### Task 7: Update ensure_article_content and generate_hn_summary for FetchedContent

**Files:**
- Modify: `trending_digest.py` — `ensure_article_content` (~line 871), `generate_hn_summary` (~line 561), `get_or_generate_hn_summary` (~line 898)

- [ ] **Step 1: Update ensure_article_content to handle FetchedContent**

Replace the existing `ensure_article_content` function with:

```python
def ensure_article_content(conn: psycopg.Connection, item: dict) -> str:
    """Fetch and cache article content if not already present.

    Returns a temp file path if the fetcher produced one (e.g. PDFs for Gemini upload),
    or empty string otherwise. Caller is responsible for deleting the temp file.
    """
    url = item.get("url") or ""
    if not url or item.get("article_content"):
        return ""

    item_id = int(item["item_id"])

    # Check DB first
    with conn.cursor() as cur:
        cur.execute("SELECT article_content FROM hn_items WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if row and row[0]:
            item["article_content"] = row[0]
            return ""

    fetched = fetch_article_content(url)
    if fetched.text:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hn_items SET article_content = %s WHERE id = %s",
                (fetched.text, item_id),
            )
        item["article_content"] = fetched.text
        logging.info("Cached article content for item %s (%d chars)", item_id, len(fetched.text))

    return fetched.file_path
```

- [ ] **Step 2: Update generate_hn_summary to accept optional file_path**

Change the function signature and add Gemini file upload logic. Replace the existing `generate_hn_summary` (currently ~line 561) with:

```python
def generate_hn_summary(item: dict, file_path: str = "") -> str:
    """Generate two-paragraph story summary for Hacker News item."""
    raw_text = item.get("text") or ""
    cleaned_text = normalize_text(BeautifulSoup(raw_text, "html.parser").get_text(" ", strip=True)) if raw_text else ""
    if len(cleaned_text) > 5000:
        cleaned_text = cleaned_text[:5000] + "..."

    article_content = item.get("article_content") or ""
    has_any_content = bool(cleaned_text or article_content)

    title = item.get("title", "")
    url = item.get("url", "")
    author = item.get("author", "")
    score = item.get("score", 0)
    comments = item.get("comment_count", 0)

    prompt = f"""Summarize this Hacker News story in exactly two paragraphs.

Title: {title}
Source URL: {url or 'N/A'}
Author: {author}
Points: {score}
Comments: {comments}
Story self-text (if available):
{cleaned_text or 'N/A'}
Article content (fetched from source URL):
{article_content or 'Could not fetch article content.'}

Write exactly two paragraphs:
1. First paragraph: Explain what the story is about and the key technical/business context.
2. Second paragraph: Explain why Hacker News readers might find it interesting or important.
{"NOTE: Article content could not be fetched. Start your summary with 'Could not fetch article content.' then do your best based on the title and URL alone." if not has_any_content else ""}
Keep each paragraph concise (3-4 sentences) and avoid hype."""

    try:
        client = get_gemini_client()
        contents: list = []

        # If we have a file (PDF/image), upload it to Gemini for direct analysis
        if file_path:
            try:
                uploaded = client.files.upload(file=file_path)
                contents.append(uploaded)
                logging.info("Uploaded file to Gemini for item %s", item.get("item_id"))
            except Exception as exc:
                logging.warning("Gemini file upload failed for item %s: %s — using text only", item.get("item_id"), exc)

        contents.append(prompt)
        response = client.models.generate_content(model=SUMMARY_MODEL, contents=contents)
        return response.text.strip()
    except Exception as exc:
        logging.exception("Hacker News summary generation failed for item %s: %s", item.get("item_id"), exc)
        return ""
```

- [ ] **Step 3: Update get_or_generate_hn_summary to pass file_path and clean up**

Replace the existing `get_or_generate_hn_summary` (~line 898) with:

```python
def get_or_generate_hn_summary(conn: psycopg.Connection, item: dict, run_day: date) -> str:
    """Return cached Hacker News summary or generate a fresh one."""
    item_id = int(item["item_id"])
    latest = get_latest_hn_summary(conn, item_id)
    if latest and summary_is_fresh(latest["generated_at"], run_day):
        return latest["summary_text"]

    file_path = ensure_article_content(conn, item)

    try:
        summary = generate_hn_summary(item, file_path=file_path)
        if summary:
            cache_hn_summary(conn, item_id, summary)
            return summary
    finally:
        if file_path:
            try:
                os.unlink(file_path)
            except OSError:
                pass

    if latest:
        return latest["summary_text"]
    return ""
```

- [ ] **Step 4: Verify the script still parses**

Run:
```bash
uv run python3 -c "import trending_digest; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add trending_digest.py
git commit -m "feat: wire up FetchedContent through ensure/generate/get_or_generate pipeline"
```

---

### Task 8: Integration test with real URLs

**Files:** No changes — manual verification only.

- [ ] **Step 1: Test full pipeline with a PDF URL**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_article_content
result = fetch_article_content(\"https://aegis.sourceforge.net/auug97.pdf\")
print(f\"Type: pdf\")
print(f\"Text: {len(result.text)} chars\")
print(f\"File: {result.file_path}\")
import os
if result.file_path: os.unlink(result.file_path)
"'
```
Expected: Non-empty text, temp file path present.

- [ ] **Step 2: Test full pipeline with an arxiv abstract URL**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_article_content
result = fetch_article_content(\"https://arxiv.org/abs/2501.12345\")
print(f\"Type: pdf (rewritten from arxiv abs)\")
print(f\"Text: {len(result.text)} chars\")
print(f\"File: {bool(result.file_path)}\")
import os
if result.file_path: os.unlink(result.file_path)
"'
```
Expected: PDF content extracted.

- [ ] **Step 3: Test full pipeline with a YouTube URL**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_article_content
result = fetch_article_content(\"https://www.youtube.com/watch?v=4d_FvgQ1csE\")
print(f\"Type: youtube\")
print(f\"Text: {len(result.text)} chars\")
print(f\"File: {result.file_path!r}\")
"'
```
Expected: Transcript text, no file path.

- [ ] **Step 4: Test full pipeline with a GitHub repo URL**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_article_content
result = fetch_article_content(\"https://github.com/SethPyle376/hiraeth\")
print(f\"Type: github\")
print(f\"Text: {len(result.text)} chars\")
print(f\"First 100: {result.text[:100]}\")
"'
```
Expected: README content.

- [ ] **Step 5: Test full pipeline with an HTML URL (existing path)**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import fetch_article_content
result = fetch_article_content(\"https://example.com\")
print(f\"Type: html\")
print(f\"Text: {len(result.text)} chars\")
"'
```
Expected: Some text extracted (or empty if fetcher is down — that's fine, it's the existing path).

- [ ] **Step 6: Test that github.io URLs go through HTML path**

Run:
```bash
bash -l -c 'uv run python3 -c "
from trending_digest import classify_url
result = classify_url(\"https://pages.github.io/some-project/\")
print(f\"Type: {result}\")
assert result == \"html\", f\"Expected html, got {result}\"
print(\"PASS\")
"'
```
Expected: `PASS`

- [ ] **Step 7: Commit (no changes, just verification)**

No commit needed — this task is verification only.

---

### Task 9: Run full digest and verify

**Files:** No code changes.

- [ ] **Step 1: Do a dry run of the full digest**

Run:
```bash
bash -l -c 'cd /home/flog99/dev/github-trending-digest && uv run python3 trending_digest.py --regenerate-only'
```
Expected: Completes without errors. The 29 items from `backfill-item-ids.txt` that had deleted summaries should get new content fetched and fresh summaries generated when they appear in the top-10 regeneration path.

Note: `--regenerate-only` uses `allow_summary_generation=False`, so it won't regenerate the 29 items. This step just verifies nothing is broken.

- [ ] **Step 2: Manually trigger summary regeneration for the 29 backfill items**

Run:
```bash
bash -l -c 'uv run python3 -c "
import trending_digest as td
from datetime import date

conn = td.get_db_connection()
td.init_db(conn)

backfill_ids = [
    47242637, 47052941, 47055262, 47082496, 47082854, 47088037, 47104667,
    47117169, 47133055, 47140042, 47143755, 47155375, 47170030, 47176239,
    47201816, 47206082, 47211830, 47212355, 47212576, 47227999, 47241976,
    47123689, 47167763, 47225318, 47230710, 47343902, 47645432, 47679258,
    47791282,
]

today = date.today()
for item_id in backfill_ids:
    with conn.cursor(row_factory=td.dict_row) as cur:
        cur.execute(\"\"\"
            SELECT hi.id as item_id, hi.title, hi.url, hi.author,
                   hi.score, hi.comment_count, hi.text, hi.article_content
            FROM hn_items hi WHERE hi.id = %s
        \"\"\", (item_id,))
        row = cur.fetchone()
    if not row:
        print(f\"Item {item_id} not found, skipping\")
        continue
    summary = td.get_or_generate_hn_summary(conn, dict(row), today)
    status = \"OK\" if summary and not summary.startswith(\"Could not fetch\") else \"NO CONTENT\"
    print(f\"{status}: {item_id} - {row[\"title\"][:60]}\")
conn.close()
"'
```
Expected: Most items show `OK` with summaries based on actual content.

- [ ] **Step 3: Run a full regeneration to update published pages**

Run:
```bash
bash -l -c 'cd /home/flog99/dev/github-trending-digest && uv run python3 trending_digest.py --regenerate-only'
```
Expected: Historical pages regenerated with the new summaries. Git diff should show updated HTML for the pages containing those 29 items.

- [ ] **Step 4: Review the changes and commit**

Run:
```bash
cd /home/flog99/dev/github-trending-digest && git diff --stat
```

Review that only expected pages changed, then:
```bash
git add docs/
git commit -m "feat: regenerate pages with enhanced content fetching for PDFs, YouTube, GitHub"
```

---

### Task 10: Final cleanup

**Files:**
- Remove: `docs/superpowers/specs/backfill-item-ids.txt` (no longer needed)

- [ ] **Step 1: Remove backfill IDs file**

```bash
rm docs/superpowers/specs/backfill-item-ids.txt
```

- [ ] **Step 2: Final commit**

```bash
git add -A
git commit -m "chore: remove backfill item IDs (regeneration complete)"
```
