---
name: arxiv-daily
description: Manage an automated arXiv paper tracking system that fetches papers daily by keywords, finds code repositories, and generates markdown reports. Use this skill whenever the user needs to add or remove paper keywords, modify search filters, run the paper fetch, update paper code links, configure GitHub Actions for daily automation, troubleshoot arxiv API or git issues, or understand how the arxiv-daily system works. Always use this skill when the user mentions arxiv papers, daily paper tracking, cv-arxiv-daily, paper keywords, or anything related to their automated paper collection setup.
---

# arXiv Daily Paper Tracker

This project automates daily arXiv paper discovery based on configurable keywords. It fetches papers, finds associated code repositories (Hugging Face → GitHub), and generates markdown reports served as both README and GitHub Pages.

## Project Architecture

```
my_arXiv_daily/
├── daily_arxiv.py          # Main script: fetch papers, find code, generate markdown
├── config.yaml             # Keywords, filters, file paths, publish targets
├── requirements.txt        # Python dependencies: arxiv, requests, pyyaml
├── docs/
│   ├── cv-arxiv-daily.json       # Paper data for README
│   ├── cv-arxiv-daily-web.json   # Paper data for GitHub Pages
│   └── cv-arxiv-daily-wechat.json
├── .github/workflows/
│   ├── cv-arxiv-daily.yml        # Daily paper fetch (every 12 hours)
│   └── update_paper_links.yml    # Weekly code link refresh (Mondays)
├── README.md                     # Generated paper list (for GitHub repo display)
└── docs/index.md                 # Generated paper list (for GitHub Pages)
```

## How It Works

1. `daily_arxiv.py` reads `config.yaml` and builds arXiv API queries from the keyword filters.
2. For each paper returned, it first checks **Hugging Face** for associated repos (spaces → models → datasets), then falls back to **GitHub** search (title → arXiv ID → code search in README files).
3. Results are saved to JSON files, then converted to markdown tables grouped by keyword topic.
4. GitHub Actions runs this on schedule (every 12 hours for new papers, weekly for link updates).

## Common Tasks

### Adding or Modifying Keywords

Edit `config.yaml` under the `keywords` section. Each keyword group has a list of `filters` that become arXiv search terms joined by OR:

```yaml
keywords:
  "Robot & Agent":
    filters: ["Embodied Agent", "Robot Learning", "Human-Robot"]
  "Your New Topic":
    filters: ["Exact Phrase Match", "Another Term"]
```

**Why this matters:** Each filter becomes `all:"filter text"` in the arXiv query. Multi-word phrases with spaces or hyphens get auto-quoted. Filters within a group are OR-combined, so a paper matching *any* filter appears under that topic.

After editing, push the changes — the GitHub Action will pick them up on the next scheduled run.

### Running the Paper Fetch Locally

```bash
python daily_arxiv.py
```

With a custom config:
```bash
python daily_arxiv.py --config_path /path/to/config.yaml
```

To **only refresh code links** (no new paper fetch):
```bash
python daily_arxiv.py --update_paper_links
```

### Understanding the config.yaml Options

| Field | Purpose |
|-------|---------|
| `max_results` | Max papers fetched per keyword group |
| `publish_readme` | Generate `README.md` |
| `publish_gitpage` | Generate `docs/index.md` |
| `publish_wechat` | Generate `docs/wechat.md` |
| `show_badge` | Include GitHub stats badges |
| `json_readme_path` | Where to persist README paper data |
| `md_readme_path` | Output markdown path |

### Configuring GitHub Actions

Two workflows live in `.github/workflows/`:

1. **cv-arxiv-daily.yml** — Runs every 12 hours, fetches new papers and commits changes.
2. **update_paper_links.yml** — Runs every Monday at 08:00 UTC, searches for missing code links.

**To enable:** Go to repo Settings → Actions → General → select "Read and write permissions". Then go to the Actions tab, enable the workflows, and trigger a manual run.

**To change schedule:** Edit the `cron` field. The format is `minute hour day-of-month month day-of-week`. Example: `"0 8 * * *"` runs daily at 8:00 AM UTC.

### Setting Up GitHub Pages

In repo Settings → Pages, set Source to "Deploy from a branch", select `main` branch and `/docs` folder. The page will be at `https://<username>.github.io/<repo-name>/`.

## Code Link Discovery Logic

When processing each paper, the script searches for code in this order:

1. **Hugging Face Hub API** — Queries `https://huggingface.co/api/arxiv/{arxiv_id}/repos` and prioritizes: Spaces → Models → Datasets.
2. **GitHub Repository Search** — Searches for the paper title in README/description.
3. **GitHub Repository Search by arXiv ID** — Searches for the arXiv ID in repo name/README/description.
4. **GitHub Code Search** — Searches for arXiv ID inside files named README.

The GitHub token (from `GITHUB_TOKEN` env var) is used when available to increase API rate limits.

## Troubleshooting

### arXiv API returns empty pages
The script handles `UnexpectedEmptyPageError` by retrying with ≤25 results. This is a known arXiv API quirk — no action needed.

### Missing code links
Not all papers have public code repositories. The weekly update workflow (`--update_paper_links`) re-scans papers that currently show "null" for code. This catches repos published after the paper.

### Git push fails in GitHub Actions
- Ensure "Read and write permissions" is enabled in repo Settings → Actions → General.
- Check that the `GITHUB_USER_NAME` and `GITHUB_USER_EMAIL` env vars in the workflow YAML match your account.
- The `github-actions-x/commit` action handles the commit; make sure the token is `${{ secrets.GITHUB_TOKEN }}`.

### Papers appear under wrong topics
This happens when a paper matches filters in multiple keyword groups — it's expected behavior. Each paper appears under every topic whose filters it matches.

### Rate limiting
GitHub API (without token): 60 requests/hour. GitHub API (with token): 5000 requests/hour. The `GITHUB_TOKEN` secret is automatically available in GitHub Actions. For local runs, set `GITHUB_TOKEN` in your environment.

## Modifying the Script

When editing `daily_arxiv.py`, keep these conventions:
- Logging uses `logging.info/warning/error` — do not use `print()` for diagnostics.
- The `http_get` helper handles retries and backoff — use it for all HTTP calls.
- Paper IDs are stripped of version suffixes (e.g., `2108.09112v1` → `2108.09112`) for consistent dedup.
- JSON data files in `docs/` use `paper_key` (versionless arXiv ID) as the primary key.
