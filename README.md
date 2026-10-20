# kurator

Self-hosted, distraction-free video feed for your YouTube subscriptions. No Shorts, no
algorithm – just the long-form videos you actually care about, filtered by your own rules
and personal recommendations that learn from what you watch. Runs as a Docker container,
works with local LLMs or OpenRouter.

> **Run kurator only in your home network or via VPN.** It is not hardened for public
> exposure on the internet.

## Status

kurator is under active development, see the [roadmap](docs/ROADMAP.md).
Phase 0 (foundation & multi-user) is done: login (local and optional OIDC), user
management, admin status page, background worker, automatic migrations with backups, Docker
image for amd64/arm64. The subscription feed follows in phase 1.

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
