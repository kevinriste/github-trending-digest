# Enhanced Content Fetching for HN Digest Items

**Date:** 2026-04-16
**Status:** Draft

## Problem

The HN digest fetcher pipeline (Puppeteer + trafilatura) only handles HTML pages. URLs pointing to PDFs, YouTube videos, GitHub repos/issues/PRs, and arxiv papers either fail silently or return empty content. This affects summary quality — the LLM falls back to summarizing from the title alone.

Current state across digest items:

| URL type | Count | Fetched | Issue |
|----------|-------|---------|-------|
| PDF | 66 | 0% | Binary file, not HTML |
| YouTube | 136 | 2% | JS-heavy, no transcript extraction |
| GitHub | 798 | 6.5% | JS-rendered, API not used |
| arxiv | 68 | 4.4% | Abstract page only, paper PDF ignored |
| HTML | 8816 | 4% | Working (low rate due to summary caching) |

## Approach

Extend `fetch_article_content` with URL-type dispatch. Before falling through to the existing Puppeteer/trafilatura path, classify the URL and route to a specialized fetcher. All fetchers return a `FetchedContent` dataclass. The existing single-file architecture is preserved.

## URL Classification & Routing

```
fetch_article_content(url)
  |- is_pdf_url(url)        -> fetch_pdf_content(url)
  |- is_arxiv_abstract(url) -> rewrite to PDF URL -> fetch_pdf_content(url)
  |- is_youtube_url(url)    -> fetch_youtube_transcript(url)
  |- is_github_url(url)     -> fetch_github_content(url)
  |- else                   -> existing Puppeteer/trafilatura path
```

Classification rules:
- **PDF**: URL path ends in `.pdf`, or matches `arxiv.org/pdf/`
- **arxiv abstract**: `arxiv.org/abs/` -> rewrite to `arxiv.org/pdf/` -> PDF path
- **YouTube**: hostname contains `youtube.com` or `youtu.be` (but skip non-video pages like `/premium`, `/?app=desktop`)
- **GitHub**: hostname is `github.com` or `gist.github.com` (NOT `*.github.io` — those are normal HTML)
- **raw.githubusercontent.com**: route by file extension — `.pdf` to PDF fetcher, otherwise download raw text directly
- **Everything else**: existing HTML pipeline

## Return Type

```python
@dataclass
class FetchedContent:
    text: str              # extracted text for DB caching in article_content
    file_path: str = ""    # temp file path for Gemini file upload (PDFs, images)
```

All fetchers return this. HTML/YouTube/GitHub fetchers set `text` only. PDF/image fetchers set both `text` (via markitdown) and `file_path` (for Gemini upload).

The existing `fetch_article_content` call sites need updating to handle this dataclass instead of a bare string. `ensure_article_content` stores `text` in the DB. `generate_hn_summary` checks `file_path` to decide whether to upload a file to Gemini.

## PDF Fetcher

1. Download PDF bytes via `requests.get(url)` (max 50MB, skip larger)
2. Save to temp file
3. Use `markitdown` to extract text -> store in `article_content` (truncated to `HN_ARTICLE_CONTENT_MAX_CHARS`)
4. Return `FetchedContent(text=extracted_text, file_path=temp_path)`

For arxiv `abs/` URLs, rewrite to `pdf/` URL first.

The temp file lifecycle is managed by `get_or_generate_hn_summary`: it passes the `file_path` into `generate_hn_summary`, then deletes the temp file after the summary is generated (whether successfully or not).

## YouTube Fetcher

1. Extract video ID from URL:
   - `youtube.com/watch?v=ID` -> parse query param `v`
   - `youtube.com/shorts/ID` -> parse path segment
   - Non-video pages (`/premium`, `/?app=desktop`) -> skip, return empty
2. Call `YouTubeTranscriptApi().fetch(video_id, languages=["en"])`
3. If `NoTranscriptFound` for English, retry without language filter to get any available transcript
4. Join transcript segments into plain text
5. Return `FetchedContent(text=transcript_text)`

No Gemini file upload needed — transcripts are text.

## GitHub Fetcher

Uses GitHub REST API (+ GraphQL for discussions). Token obtained via `subprocess.run(["gh", "auth", "token"])`, cached for the duration of the run.

### URL sub-classification and API mapping

```
fetch_github_content(url)
  |- gist.github.com/{user}/{id}                  -> GET /gists/{id}
  |- github.com/{o}/{r}/issues/{n}                 -> GET /repos/{o}/{r}/issues/{n}
  |- github.com/{o}/{r}/pull/{n}                   -> GET /repos/{o}/{r}/pulls/{n}
  |- github.com/{o}/{r}/discussions/{n}            -> GraphQL query
  |- github.com/{o}/{r}/commit/{sha}               -> GET /repos/{o}/{r}/commits/{sha}
  |- github.com/{o}/{r}/releases/tag/{t}           -> GET /repos/{o}/{r}/releases/tags/{t}
  |- github.com/{o}/{r}/releases (no tag)          -> GET /repos/{o}/{r}/releases (latest)
  |- github.com/{o}/{r}/security/advisories/{id}   -> GET /repos/{o}/{r}/security-advisories/{id}
  |- github.com/{o}/{r}/blob/{ref}/{path}
  |    |- if .pdf extension                        -> download raw URL -> PDF fetcher
  |    |- else                                     -> GET /repos/{o}/{r}/contents/{path}?ref={ref}
  |- github.com/{o}/{r}/tree/{ref}/{path}          -> GET /repos/{o}/{r}/readme/{path}?ref={ref}
  |- github.com/{o}/{r}                            -> GET /repos/{o}/{r}/readme
  |- github.com/{user} (org/user profile)          -> skip
```

### Content assembly per type

| Type | Returned text |
|------|--------------|
| repo | README markdown (base64-decoded) |
| issue | `"Issue: {title}\n\n{body}"` |
| pr | `"Pull Request: {title}\n\n{body}"` |
| discussion | `"Discussion: {title}\n\n{body}"` |
| commit | `"Commit: {message}\n\nFiles changed: {file_list}\nStats: +{additions} -{deletions}"` |
| release | `"Release: {name}\n\n{body}"` |
| security advisory | `"Security Advisory ({severity}): {summary}\n\n{description}"` |
| file | Raw decoded content, truncated to `HN_ARTICLE_CONTENT_MAX_CHARS` |
| directory | Subdirectory README if present, else file listing |
| gist | Concatenated file contents |

### Rate limiting

GitHub API allows 5,000 requests/hour with authentication. The daily digest processes ~10 items, so rate limits are not a practical concern. Add basic retry with exponential backoff (3 attempts: 1s, 2s, 4s) and respect `429`/`403` rate limit responses.

## Gemini File Upload Integration

In `generate_hn_summary`, when `file_path` is present on the fetched content:

```python
uploaded_file = client.files.upload(file=file_path)
response = client.models.generate_content(
    model=SUMMARY_MODEL,
    contents=[uploaded_file, prompt]
)
```

When `file_path` is absent, use the existing text-only prompt path unchanged.

Confirmed: `gemini-3.1-flash-lite` supports `files.upload` and can read PDFs.

If the Gemini upload fails, fall back to the text-only path using the markitdown-extracted content.

## Dependencies

New additions to `pyproject.toml`:
- `markitdown` — PDF/document text extraction
- `youtube-transcript-api` — YouTube transcript fetching

No new deps for GitHub (uses existing `requests` + `subprocess` for `gh auth token`).

## Error Handling

All failures return empty `FetchedContent`. No new fetcher can crash the digest run.

A shared retry helper with exponential backoff (3 attempts, 1s/2s/4s base delay):

| Fetcher | Failure modes | Behavior |
|---------|--------------|----------|
| PDF | Download fails, markitdown can't parse, file too large, Gemini upload fails | Log warning, return empty. If markitdown fails but download succeeded, still upload to Gemini. |
| YouTube | `NoTranscriptFound`, video unavailable, non-video URL | Log warning, return empty. |
| GitHub | 404 (deleted/private), rate limited, GraphQL error | Log warning, return empty. |
| arxiv | PDF not available yet | Log warning, return empty. |

## Backfill

Already done. 26 cached summaries were deleted on 2026-04-16 for items that had empty `article_content` and matched newly-supported URL types. The next run after implementation will re-fetch content and regenerate summaries for those items automatically via the existing `get_or_generate_hn_summary` flow.

## Files Modified

- `trending_digest.py` — all changes in the single main script:
  - New `FetchedContent` dataclass
  - New functions: `fetch_pdf_content`, `fetch_youtube_transcript`, `fetch_github_content`, `retry_fetch`, `extract_youtube_id`, `classify_github_url`
  - Modified: `fetch_article_content` (add dispatch), `ensure_article_content` (handle dataclass), `generate_hn_summary` (Gemini file upload path)
- `pyproject.toml` — add `markitdown` and `youtube-transcript-api` dependencies
