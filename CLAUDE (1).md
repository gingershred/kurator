# CLAUDE.md – Projektkontext für Claude Code (kurator)

> Diese Datei beschreibt Ziel, Architektur und Arbeitsregeln von **kurator**.
> Die Phasen mit Abnahmekriterien stehen in `docs/ROADMAP.md`. Lies beide
> Dateien vollständig, bevor du Code schreibst.

## Ziel

**kurator** ist ein selbst gehostetes, gefiltertes Frontend für YouTube-Abos, das als Docker-Container läuft. Mehrere Nutzer loggen sich ein und sehen jeweils ihren eigenen ruhigen, gefilterten Feed:

- **Keine Shorts.** Niemals, an keiner Stelle.
- **Abo-Feed als Kern:** neue Videos der abonnierten Kanäle, chronologisch oder nach Relevanz.
- **Uninteressantes ausblenden:** über Regeln und über gelerntes Feedback.
- **Eigene Empfehlungen:** auf Basis von Schauverhalten und Abos, nicht auf Basis von YouTubes Algorithmus.
- **Wiedergabe wählbar:** eingebetteter Player oder lokal heruntergeladene Datei.

Das Projekt ist öffentlich auf GitHub, lizenziert unter **GNU AGPLv3** (Lizenzdatei liegt bereits im Repo), und soll für andere Selfhoster nachvollziehbar und leicht installierbar sein.

## Rahmenbedingungen

- **Mehrbenutzerfähig von Anfang an.** Jeder Nutzer hat eigene Abos, Filterregeln, Verlauf, Bewertungen, Profil und Einstellungen. Kanäle, Videos, Metadaten, Embeddings und Downloads werden nutzerübergreifend geteilt (einmal abrufen, mehrfach nutzen).
- **Rollen:** `admin` (verwaltet Nutzer und globale Einstellungen) und `user`. Der erste Admin wird beim Start aus den Umgebungsvariablen angelegt. Keine öffentliche Registrierung; Nutzer legt der Admin an.
- **Login standardmäßig lokal** (Benutzername + Passwort). OIDC ist optional zuschaltbar.
- **Nur Heimnetz/VPN.** kurator wird nicht öffentlich ins Internet gestellt. Daher kein 2FA nötig; trotzdem sichere Grundlagen: bcrypt/argon2, httponly-Session-Cookies, CSRF-Schutz, einfaches Rate-Limiting beim Login. Die Doku weist ausdrücklich darauf hin, kurator nicht öffentlich zu exponieren.
- **Zielplattform zunächst: Desktop-Browser.** Layout desktop-first, darf auf schmalen Bildschirmen aber nicht kaputtgehen. Eine Handy-App kommt später – deshalb Geschäftslogik strikt in Service-Module trennen (nicht in Routen/Templates), damit später eine JSON-API darauf aufsetzen kann.

## Grundprinzipien

1. **Offizielle, stabile Wege zuerst.** Metadaten kommen aus YouTube-RSS-Feeds und der YouTube Data API v3. Kein Scraping von youtube.com-Seiten, kein Nachbau von Invidious/Piped. Einzige Ausnahme: das optionale Download-Modul (yt-dlp).
2. **Shorts werden an der Quelle verhindert:** Kanal-Uploads werden über die Auto-Playlist `UULF<Kanal-ID ohne UC>` gelesen (nur lange Videos). Zusätzlicher Fallback-Filter: Videos ≤ `SHORTS_MAX_SECONDS` (Standard 180 s) werden als `is_short` markiert und nie angezeigt.
3. **Livestreams, Premieren und Mitgliedervideos sind standardmäßig ausgeblendet** (pro Nutzer umschaltbar).
4. **Quota ist knapp (10.000 Einheiten/Tag, für alle Nutzer gemeinsam).** RSS kostet keine Quota und ist die primäre Quelle. `videos.list` wird gebündelt (bis 50 IDs/Aufruf). `search.list` (100 Einheiten) nur im Entdecken-Modul und nur innerhalb eines konfigurierbaren Tagesbudgets, das fair auf aktive Nutzer verteilt wird. Jeder API-Aufruf wird in einer Quota-Tabelle verbucht; ist das Budget erreicht, wird nicht mehr aufgerufen.
5. **Daten gehören den Nutzern.** Alle Zustände (Datenbank, Modelle, Downloads, Backups) liegen im Volume `/data`. Das Image enthält nur Code. Updates dürfen niemals Nutzerdaten verlieren.
6. **Privatsphäre:** Eingebettete Wiedergabe über `youtube-nocookie.com`. Kein Google-Konto nötig. Externe LLM-Anbieter nur, wenn explizit konfiguriert.
7. **Einfach vor clever.** Wenig Abhängigkeiten, wenig JavaScript, lesbarer Code.

## Tech-Stack (verbindlich)

| Bereich | Wahl |
|---|---|
| Sprache | Python 3.12 |
| Web | FastAPI, Jinja2-Templates, HTMX, minimales eigenes CSS (kein schweres Framework) |
| DB | SQLite im WAL-Modus unter `/data/kurator.db`, SQLAlchemy 2.x, Alembic-Migrationen |
| Hintergrundjobs | eigener Worker-Prozess (gleiches Image, anderes Startkommando) mit APScheduler; Job-Warteschlange in der DB (für Downloads etc.) |
| HTTP | httpx (async), feedparser für RSS |
| Downloads | yt-dlp + ffmpeg im Image (plus ggf. eine von yt-dlp benötigte JavaScript-Runtime – aktuelle yt-dlp-Doku prüfen); Wiedergabe per HTML5-`<video>` mit HTTP-Range-Unterstützung |
| Embeddings | lokal mit `fastembed` (ONNX, kein PyTorch), Standardmodell mehrsprachig (z. B. `intfloat/multilingual-e5-small`), Modelle gecacht unter `/data/models` |
| LLM | OpenAI-kompatibler Client (`openai`-SDK mit `base_url`), funktioniert mit OpenRouter, Ollama, LM Studio usw. Optional – kurator läuft vollständig ohne LLM. |
| Auth | lokaler Login (argon2 oder bcrypt), Session-Cookie, CSRF-Schutz, Login-Rate-Limit. Optional OIDC (authlib) für zentrale Logins wie Authentik/Authelia/Keycloak. |
| Tests | pytest, pytest-asyncio, respx zum Mocken von HTTP |
| Qualität | ruff (lint + format), mypy im strikten Modus für `app/` |
| Container | ein Dockerfile (multi-stage, non-root user), `docker-compose.yml` mit Diensten `web` und `worker`, Healthcheck |
| CI/CD | GitHub Actions: lint + test bei jedem Push/PR; bei Push auf `main`, bei Tags `v*` und wöchentlich per Zeitplan (damit yt-dlp aktuell bleibt) Multi-Arch-Build (amd64 + arm64) nach `ghcr.io/<owner>/kurator` |

Abweichungen vom Stack nur nach Rückfrage.

## Konfiguration (Umgebungsvariablen, `.env`)

Umgebungsvariablen enthalten nur Infrastruktur und Startwerte. Alles, was Nutzer oder Admin im Betrieb ändern wollen, liegt in der Datenbank und ist in der UI einstellbar.

```env
# Basis
KURATOR_SECRET_KEY=           # Pflicht, Session-Signatur
KURATOR_BASE_URL=http://kurator.lan:8080
KURATOR_TIMEZONE=Europe/Berlin
KURATOR_LANGUAGE=de
KURATOR_DATA_DIR=/data
KURATOR_SECURE_COOKIES=false  # true, wenn per HTTPS (z. B. Reverse Proxy im LAN)

# Erster Admin (wird nur angelegt, wenn noch kein Nutzer existiert)
KURATOR_ADMIN_USERNAME=admin
KURATOR_ADMIN_PASSWORD=       # nur beim ersten Start nötig, danach entfernen

# Login
KURATOR_AUTH_MODE=local       # local | oidc | both
OIDC_ISSUER=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=

# YouTube
YOUTUBE_API_KEY=              # optional (dann nur RSS-Daten), empfohlen
QUOTA_DAILY_BUDGET=8000       # Sicherheitsabstand zu 10.000, gilt für alle Nutzer zusammen
QUOTA_SEARCH_BUDGET=2000      # davon max. für search.list
FEED_POLL_MINUTES=30
SHORTS_MAX_SECONDS=180

# Downloads (Standardwerte, im Admin-Bereich änderbar)
DOWNLOADS_ENABLED=false
DOWNLOAD_MAX_QUALITY=1080
DOWNLOAD_MAX_STORAGE_GB=50
DOWNLOAD_RETENTION_DAYS=14    # nach "gesehen" von allen Anfordernden

# LLM (optional)
LLM_ENABLED=false
LLM_BASE_URL=                 # z. B. https://openrouter.ai/api/v1 oder http://ollama:11434/v1
LLM_API_KEY=
LLM_MODEL=

# Embeddings
EMBEDDING_PROVIDER=local      # local | openai_compatible
EMBEDDING_MODEL=intfloat/multilingual-e5-small
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
```

Alle Einstellungen werden über eine zentrale `Settings`-Klasse (pydantic-settings) geladen und beim Start validiert. Eine `.env.example` mit Kommentaren ist Pflicht. Geheimnisse niemals ins Repo, niemals ins Log.

## Projektstruktur (Zielbild)

```
app/
  main.py              # FastAPI-App, Router-Registrierung
  config.py            # Settings
  db.py                # Engine, Session, WAL
  models/              # SQLAlchemy-Modelle
  services/            # Geschäftslogik (von Web und späterer API genutzt)
  auth/                # local + oidc, Rollen, Rate-Limit
  youtube/
    rss.py             # UULF-Feeds lesen
    api.py             # Data-API-Client mit Quota-Buchung
    quota.py
    ids.py             # Kanal-ID/Handle/URL-Parsing, UC->UULF
  feed/                # Feed-Abfrage, Filterregeln
  playback/
    embed.py
    downloads.py       # yt-dlp-Anbindung, Speicherverwaltung, Aufräumen
  recommend/           # Embeddings, Profil, Ranking, Kandidaten
  llm/                 # OpenAI-kompatibler Client, Prompts
  importers/           # Google-Takeout-CSV/JSON
  web/
    routes/
    templates/
    static/
  worker.py            # Scheduler + Job-Queue
  cli.py               # z. B. create-user, reset-password, import, backup, reindex
migrations/            # Alembic
tests/
docs/
  ROADMAP.md
  INSTALL.md
Dockerfile
docker-compose.yml
.env.example
```

## Datenmodell (Ausgangspunkt, darf verfeinert werden)

**Nutzerbezogen**
- `user` – Benutzername, Passwort-Hash, Rolle (`admin`/`user`), aktiv, OIDC-Subject (optional), erstellt, letzter Login.
- `user_setting` – Schlüssel/Wert pro Nutzer (z. B. Standard-Wiedergabe `embed`/`download`, Livestreams anzeigen, Feed-Sortierung).
- `subscription` – user ↔ channel, Status (aktiv/stummgeschaltet), Quelle (import/manuell/empfehlung).
- `user_video_state` – gesehen, Fortschritt (Sekunden), ausgeblendet (+ Grund), Bewertung (+1/−1), „später ansehen“.
- `watch_event` – Rohereignisse vom Player (play, pause, progress, ended) für das Lernen.
- `filter_rule` – pro Nutzer: Typ (keyword_title, keyword_desc, channel, min_duration, max_duration, regex), Wert, aktiv.
- `user_profile` – Profilvektor, Freitext-Interessenbeschreibung, Stand.
- `recommendation` – pro Nutzer: Kandidat, Score, Quelle, Begründung, Status.

**Geteilt**
- `channel` – youtube_channel_id, Titel, Handle, Avatar, letzter Abruf, Fehlerstatus.
- `video` – youtube_video_id, channel, Titel, Beschreibung, veröffentlicht, Dauer, Thumbnail, Tags, Kategorie, `is_short`, `is_live`, `is_premiere`, `is_members_only`.
- `video_embedding` – Vektor (als BLOB) + Modellname.
- `download` – video, Status (queued/running/done/failed/deleted), Dateipfad, Größe, Qualität, Fehlertext, angefordert von (n:m zu `user`), zuletzt abgespielt.
- `global_setting` – vom Admin änderbare Werte (Downloads an/aus, Speicherlimit, Quota-Budgets …).
- `quota_ledger` – Datum, Methode, Einheiten, auslösender Nutzer (optional).
- `job` / `job_run` – Warteschlange und Historie der Hintergrundjobs mit Status und Fehlertext.

## Arbeitsregeln für Claude Code

1. **Phasenweise arbeiten.** Nur die Phase umsetzen, die der Nutzer nennt. Am Anfang jeder Phase einen kurzen Plan vorlegen (Dateien, Modelle, offene Fragen) und auf Freigabe warten. Danach nicht ungefragt in die nächste Phase springen.
2. **Kleine, saubere Commits** mit aussagekräftigen Nachrichten (Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`).
3. **Tests zu jeder Logik.** Insbesondere: ID-Parsing, Shorts-Erkennung, Filterregeln, Quota-Buchung, Rechte/Isolation zwischen Nutzern, Download-Aufräumlogik, Ranking. Externe Aufrufe (YouTube, yt-dlp, LLM) immer mocken – Tests dürfen kein Netz brauchen.
4. **Nutzerisolation ist Pflicht.** Jede Abfrage nutzerbezogener Daten filtert nach dem angemeldeten Nutzer. Für jede neue Route gibt es einen Test, dass Nutzer B die Daten von Nutzer A weder sehen noch ändern kann.
5. **Jede Schemaänderung ist eine Alembic-Migration.** Migrationen laufen beim Containerstart automatisch. Vor jeder Migration legt kurator ein Backup der Datenbank unter `/data/backups/` an (die letzten 10 behalten).
6. **Fehlertoleranz:** Ein kaputter Feed, ein API- oder Download-Fehler darf nie den ganzen Lauf abbrechen. Fehler loggen, im Job vermerken, weitermachen.
7. **Vor Abschluss einer Phase:** `ruff check`, `ruff format --check`, `mypy app`, `pytest` müssen grün sein; `docker compose build` muss funktionieren; README/INSTALL aktualisieren.
8. **UI-Sprache Deutsch** (Texte zentral gesammelt, damit später übersetzbar), Code, Bezeichner, Kommentare und Commit-Messages Englisch.
9. **Keine Annahmen über undokumentierte YouTube-Interna** außer den hier freigegebenen (UULF-Playlists, RSS-Feeds, yt-dlp im Download-Modul). Wenn etwas anderes nötig erscheint: nachfragen.
10. Bei Unklarheiten lieber eine kurze Rückfrage als eine stille Annahme. Getroffene Annahmen am Ende der Phase auflisten.

## Nützliche Befehle (anlegen und hier aktuell halten)

```bash
docker compose up -d --build
docker compose exec web python -m app.cli create-user <name> [--admin]
docker compose exec web python -m app.cli reset-password <name>
docker compose exec web python -m app.cli import-takeout <name> /data/import/subscriptions.csv
docker compose exec web alembic upgrade head
pytest
ruff check . && ruff format --check . && mypy app
```
