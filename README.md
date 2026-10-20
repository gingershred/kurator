# kurator

Self-hosted, distraction-free video feed for your YouTube subscriptions. No Shorts, no
algorithm – just the long-form videos you actually care about, filtered by your own rules
and personal recommendations that learn from what you watch. Runs as a Docker container,
works with local LLMs or OpenRouter.

> **Run kurator only in your home network or via VPN.** It is not hardened for public
> exposure on the internet.

## Status

kurator is under active development, see the [roadmap](docs/ROADMAP.md).

Working today (phases 0, 1 and 1.1):

- **Subscription feed without Shorts** – channels are read via YouTube's official long-form
  uploads playlist (RSS, no API quota); with a YouTube API key, durations are added and
  anything up to 180 s is filtered out as a fallback.
- Add channels by ID, URL or @handle, or import your Google Takeout `subscriptions.csv` or an
  OPML file; export your subscriptions as OPML.
- Robust fetching: when YouTube's RSS endpoint fails, channels are fetched via the official
  Data API instead (within a fixed share of the daily quota).
- Feed, "watch later" and "hidden" views; mark as watched, hide, rate; filter by channel and
  length; keyboard shortcuts (`j`/`k`, `w`, `h`, `l`, `?`).
- Personal filter rules (keywords, regex, channel, min/max duration) – always undoable.
- Embedded player via `youtube-nocookie.com` that remembers your progress; thumbnails are
  cached locally, so your browser never talks to Google while browsing the feed.
- Multi-user with local login or OIDC, admin user management and status page.

The user interface is German for now; texts are collected centrally for later translation.

## Quick start

```bash
mkdir -p /opt/kurator && cd /opt/kurator
curl -fsSLO https://raw.githubusercontent.com/gingershred/kurator/main/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/gingershred/kurator/main/.env.example -o .env
mkdir -p data && sudo chown 1000:1000 data
# edit .env: KURATOR_SECRET_KEY, KURATOR_ADMIN_PASSWORD, KURATOR_BASE_URL
docker compose pull && docker compose up -d
```

Then open `http://<host>:8080`. Full guide (updates, backup/restore, OIDC, HTTPS):
**[docs/INSTALL.md](docs/INSTALL.md)** (German).

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # installs Python 3.12 + dependencies into .venv
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy app

# run locally
export KURATOR_SECRET_KEY=dev-secret-key-change-me KURATOR_DATA_DIR=./data \
       KURATOR_ADMIN_PASSWORD=dev-password-1
uv run python -m app.cli serve      # web UI on http://localhost:8080
uv run python -m app.cli worker     # background worker (second terminal)
```

Architecture and working rules: [CLAUDE.md](CLAUDE.md).

## License

kurator is free software under the [GNU Affero General Public License v3.0](LICENSE).
