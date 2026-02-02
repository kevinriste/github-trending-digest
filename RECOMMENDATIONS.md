# Recommendations for Future Improvements

Ideas for enhancing GitHub Trending Digest, ranging from quick wins to larger features.

---

## Quick Wins

### 1. Add Repository Topics/Tags
GitHub repos have topics (tags) that could be displayed on each card. Would help readers quickly identify repo categories.

**Implementation:** Update `scrape_trending_repos()` to extract topic badges from the trending page HTML.

### 2. Cache AI Summaries
If the script runs multiple times in a day (e.g., after a failure), it regenerates summaries for repos that haven't changed. Caching summaries by repo name + commit SHA would reduce API costs.

**Implementation:** Store summaries in a JSON file keyed by `{repo_name}:{latest_commit_sha}`.

### 3. Add "Stars This Week" Metric
The trending page shows daily stars but also has "this week" data. Including both would give better context.

### 4. RSS Feed
Generate an RSS/Atom feed alongside the HTML for people who prefer feed readers.

**Implementation:** Add `generate_rss_feed()` function that creates `docs/feed.xml`.

---

## Medium Effort

### 5. Historical Trending Analysis
Track which repos appear multiple days in a row. Could add badges like "Trending for 3 days" or "New today".

**Implementation:** Store repo appearances in `pages.json`, cross-reference when generating daily pages.

### 6. Category Filtering
Allow filtering repos by language or topic on the index page (client-side JavaScript).

**Implementation:** Add a simple filter UI with checkboxes that show/hide repo cards based on data attributes.

### 7. Comparison View
Show what changed from yesterday—new entries, repos that fell off, rank changes.

**Implementation:** Load previous day's data and generate a diff section at the top of each daily page.

### 8. Multiple AI Models
Compare summaries from different models (Claude vs GPT) side-by-side, or let users toggle between them.

---

## Larger Features

### 9. Weekly/Monthly Rollup
Create aggregate pages showing the most frequently trending repos over longer periods.

**Implementation:** New script that analyzes `pages.json` and generates `docs/week/` and `docs/month/` pages.

### 10. GitHub Actions Migration
Move from crontab to GitHub Actions for:
- Better visibility into run history
- No need to maintain a server
- Automatic retries on failure

**Trade-off:** Loses Gotify integration, would need alternative notification mechanism.

### 11. Configurable Email Recipients
Support multiple recipients or make the email target configurable via environment variable.

### 12. Alternative Notification Channels
Add Discord webhook, Slack, or Telegram bot notifications in addition to email.

---

## Ideas We Discussed But Didn't Implement

### Repo README Preview
Show a truncated version of the README directly on the page instead of just the AI summary. Decided against because it would make pages very long and the AI summary captures the essence.

### Star History Graphs
Embed star history charts for each repo. Decided against due to complexity and external API dependencies.

### User Preferences
Let users configure which languages/topics to track. Would require a backend and user accounts—out of scope for a static site.

---

## Known Limitations

1. **GitHub Rate Limiting**: The script makes ~10 README fetches per run. Heavy usage could hit rate limits. Consider adding GitHub API authentication for higher limits.

2. **trafilatura Sensitivity**: The HTML structure is carefully designed for trafilatura compatibility. Changes to the template should be tested with the podcast-transcribe pipeline.

3. **Single Timezone**: Dates are based on server timezone. A global audience might see "today's" digest before their local date changes.

4. **No Retry Logic**: If scraping fails partway through, there's no partial save. The whole run fails and Gotify notifies.
