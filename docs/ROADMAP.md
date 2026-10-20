# Roadmap – kurator

Jede Phase endet mit einem lauffähigen, getesteten Stand auf `main`, der als
Image nach `ghcr.io/<owner>/kurator` gebaut wird. Eine Phase gilt erst als
fertig, wenn alle Abnahmekriterien erfüllt sind.

Zielplattform bis einschließlich Phase 5: **Desktop-Browser im Heimnetz/VPN**.

---

## Phase 0 – Fundament & Mehrbenutzer

**Ziel:** Produktionsfähiges Gerüst mit Nutzerverwaltung. Ab hier kann jede Änderung automatisch ausgerollt werden.

- Projektstruktur laut `CLAUDE.md`, `pyproject.toml`, ruff/mypy/pytest konfiguriert
- Settings-Klasse, `.env.example`; `.gitignore` um `.env` und `data/` ergänzen (falls nicht vorhanden)
- SQLite + SQLAlchemy + Alembic, WAL-Modus, Initialmigration (`user`, `user_setting`, `global_setting`, `job`, `job_run`, `quota_ledger`)
- Automatisches DB-Backup vor Migrationen, Migrationen beim Start
- **Auth:** lokaler Login/Logout, Session, CSRF, Login-Rate-Limit; erster Admin aus `.env`
- **Nutzerverwaltung (Admin):** Nutzer anlegen, deaktivieren, löschen (inkl. aller nutzerbezogenen Daten), Passwort zurücksetzen, Rolle ändern
- **Eigenes Konto:** Passwort ändern
- CLI: `create-user`, `reset-password`
- OIDC-Login als optionaler Modus (`KURATOR_AUTH_MODE=oidc|both`), Zuordnung über OIDC-Subject
- Leere Startseite hinter Login, Admin-Statusseite (`/admin/status`: Version, DB-Stand, letzte Jobs, Quota heute)
- `GET /healthz` für Docker-Healthcheck
- Dockerfile (multi-stage, non-root), `docker-compose.yml` mit `web` + `worker`, Volume `/data`
- GitHub Actions: CI (lint, typecheck, test) + Build/Push Multi-Arch nach ghcr.io (`latest`, `sha-…`, `vX.Y.Z` bei Tags, zusätzlich wöchentlicher Rebuild)
- `docs/INSTALL.md`: Installation, erster Start, automatische Updates per Cron (`docker compose pull && docker compose up -d`), Backup/Restore, deutlicher Hinweis „nur im Heimnetz/VPN betreiben“
- Lizenz (AGPLv3) liegt bereits im Repo – nur im README und im Footer der UI verlinken

**Abnahme:**
- [ ] `docker compose up` startet, erster Admin kann sich einloggen, `/healthz` liefert 200
- [ ] Admin legt einen zweiten Nutzer an; beide können sich unabhängig einloggen
- [ ] Nicht-Admins erreichen keine Admin-Seiten (Test)
- [ ] Neustart/Neuaufbau des Containers verliert keine Daten
- [ ] Push auf `main` erzeugt ein Image in ghcr.io
- [ ] Alle Checks grün

---

## Phase 1 – Abo-Feed ohne Shorts (eingebetteter Player)

**Ziel:** Der tägliche Gebrauch funktioniert für jeden Nutzer: Abos rein, Feed lesen, Videos schauen, Unerwünschtes ausblenden.

**Kanäle & Abos (pro Nutzer)**
- Kanal hinzufügen per URL, `@handle` oder Kanal-ID (Auflösung von Handles per `channels.list?forHandle=`, 1 Einheit)
- Import aus Google Takeout (`subscriptions.csv`) per Upload in der UI und per CLI
- Kanalliste: stummschalten, entfernen, letzter Abruf, Fehlerstatus
- Kanäle werden geteilt gespeichert: abonnieren zwei Nutzer denselben Kanal, wird er nur einmal abgerufen

**Abruf (Worker)**
- Alle `FEED_POLL_MINUTES` die UULF-RSS-Feeds aller Kanäle mit mindestens einem aktiven Abo abrufen (parallel, begrenzt, mit Timeout)
- Neue Video-IDs gesammelt per `videos.list` anreichern (Dauer, Tags, Kategorie, Live-/Premierenstatus), Quota verbuchen
- Shorts-Fallback: Dauer ≤ `SHORTS_MAX_SECONDS` → `is_short = true`
- Ohne API-Key: nur RSS-Daten, Shorts-Schutz allein über UULF; in der UI dezent darauf hinweisen

**Feed-UI (desktop-first)**
- Kachelraster (Thumbnail, Titel, Kanal, Dauer, Alter), Dark Mode nach Systemeinstellung, auf schmalen Fenstern nicht kaputt
- Paginierung oder Nachladen per HTMX
- Filterleiste: ungesehen / alle, Kanal, Länge
- Aktionen pro Video: ansehen, als gesehen markieren, ausblenden („interessiert mich nicht“), später ansehen, 👍/👎
- Tastaturkürzel für die häufigsten Aktionen (z. B. `j/k` navigieren, `h` ausblenden, `w` gesehen)
- Livestreams, Premieren und Mitgliedervideos standardmäßig ausgeblendet (pro Nutzer umschaltbar)

**Player**
- Detailseite mit eingebettetem Player über `youtube-nocookie.com` (IFrame API)
- Fortschritt wird über Player-Events gespeichert (`watch_event`, `user_video_state`), Fortsetzen an letzter Stelle
- Ab 90 % gesehen → automatisch „gesehen“
- Link „Auf YouTube öffnen“
- Wiedergabe-Abstraktion so anlegen, dass in Phase 2 die Download-Variante ergänzt werden kann (Einstellung `playback_mode` existiert bereits, vorerst nur `embed`)

**Filterregeln (pro Nutzer)**
- Verwaltung in den Einstellungen: Stichwörter (Titel/Beschreibung), Regex, Mindest-/Höchstdauer, Kanalausschluss
- Ausgeblendete Videos bleiben in einer Ansicht „Ausgeblendet“ einsehbar (inkl. Grund), Regeln sind rückgängig machbar

**Abnahme:**
- [ ] Takeout-Import mit > 100 Kanälen funktioniert
- [ ] Kein Short erscheint im Feed (Test mit bekannten Short-IDs und Kanälen, die viele Shorts posten)
- [ ] Ein Tag Betrieb mit 100 Kanälen bleibt deutlich unter 1.000 Quota-Einheiten
- [ ] Nutzer A sieht weder Abos noch Verlauf noch Regeln von Nutzer B (Tests)
- [ ] Fortschritt und Gesehen-Status bleiben nach Container-Update erhalten
- [ ] Tests für ID-Parsing, Shorts-Erkennung, Filterregeln, Quota

---

## Phase 1.1 – Robuster Abruf & OPML

**Ziel:** Der Feed bleibt auch bei Störungen des RSS-Endpunkts aktuell (seit Dezember 2025 liefert YouTube dort zeitweise 404/500), und Abos lassen sich mit anderen Tools austauschen.

- Vorübergehende RSS-Fehler (Timeout, 5xx) werden einmal wiederholt
- RSS-Ersatzabruf über `playlistItems.list` auf die UULF-Playlist (1 Einheit), nur mit API-Schlüssel; höchstens 25 % des Tagesbudgets, Abrufabstand pro Kanal so bemessen, dass selbst ein ganztägiger Ausfall aller Feeds im Rahmen bleibt
- Kanäle, die laut RSS **und** API nicht mehr existieren, werden markiert und nur noch wöchentlich geprüft
- Statusseite: RSS-Zustand des letzten Laufs, Ersatzabrufe heute, verschwundene Kanäle
- OPML-Import (Upload und CLI) und -Export (mit UULF-Feeds)

**Abnahme:**
- [x] Ein ganztägiger RSS-Ausfall bei 100 Kanälen bleibt innerhalb des Fallback-Anteils (Test)
- [x] Ohne API-Schlüssel kein Ersatzabruf und keine automatische Markierung als „existiert nicht mehr“
- [x] OPML aus anderen Tools wird importiert, eigener Export lässt sich wieder importieren
- [x] Abruf vieler Kanäle blockiert nicht (keine DB-Verbindung über Netzwartezeiten hinweg; Regressionstest)

---

## Phase 2 – Download-Wiedergabe

**Ziel:** Nutzer können wählen, ob ein Video eingebettet oder als lokal heruntergeladene Datei abgespielt wird.

**Admin (global)**
- Download-Funktion an/aus (Standard: aus), maximale Qualität, Speicherlimit, Aufbewahrungsdauer
- Übersicht: belegter Speicher, laufende/fehlgeschlagene Downloads, manuelles Löschen

**Nutzer**
- Einstellung „Standard-Wiedergabe“: eingebettet oder heruntergeladen
- Pro Video wählbar: „Eingebettet ansehen“ / „Herunterladen“ / „Lokal ansehen“ (wenn vorhanden)
- Optional pro Kanal: „Neue Videos automatisch herunterladen“
- Download-Status in Feed und Detailseite sichtbar (wartet, lädt x %, fertig, Fehler)

**Technik**
- yt-dlp + ffmpeg im Image; Downloads als Jobs im Worker, Parallelität begrenzt (Standard 1–2)
- Formatwahl: höchstens `DOWNLOAD_MAX_QUALITY`, Standard **MP4/H.264+AAC** (spielt auch auf iPhone/iPad; WebM/VP9 nur, wenn ausdrücklich gewählt); Datei, Thumbnail und Metadaten unter `/data/media/<channel_id>/<video_id>.*`
- **Nur-Audio-Downloads** (z. B. für Vorträge/Podcasts), abspielbar im selben Player
- **Untertitel** (falls vorhanden) mit herunterladen und als `<track>` (WebVTT) anbieten
- Wiedergabe über eigenen Endpunkt mit HTTP-Range-Unterstützung und Rechteprüfung, HTML5-`<video>`; Fortschritt wird genauso gespeichert wie beim eingebetteten Player
- Geteilte Downloads: lädt Nutzer B ein Video, das Nutzer A schon geladen hat, wird nicht erneut geladen
- Aufräumjob: löscht Dateien nach Ablauf der Aufbewahrungsdauer (gerechnet ab „gesehen“ durch alle Anfordernden), älteste zuerst bei Überschreitung des Speicherlimits; nie Dateien, die gerade abgespielt werden
- Fehlerbehandlung: verständliche Fehlermeldung in der UI, automatische Wiederholung mit Backoff, Hinweis wenn yt-dlp veraltet scheint
- Fällt der Download aus, bleibt der eingebettete Player immer als Rückfall verfügbar

**Abnahme:**
- [ ] Mit `DOWNLOADS_ENABLED=false` ist die Funktion in der UI vollständig unsichtbar
- [ ] Heruntergeladenes Video spielt im Browser inkl. Spulen ab und speichert den Fortschritt
- [ ] Speicherlimit wird eingehalten (Test der Aufräumlogik mit simulierten Dateien)
- [ ] Nutzer ohne Anforderung können keine fremden Downloads über die URL abrufen, wenn sie das Video nicht in ihrem Feed haben (Test)
- [ ] Tests für Formatwahl, Aufräumlogik, Rechteprüfung (yt-dlp gemockt)

---

## Phase 3 – Personalisierung

**Ziel:** Der Feed lernt pro Nutzer, was interessiert, und kann danach sortieren und filtern.

- Lokale Embeddings (fastembed) für Titel + Beschreibung + Tags jedes neuen Videos (geteilt); Backfill-Job für Bestand; CLI `reindex`
- Interessenprofil pro Nutzer: gewichteter Mittelwert positiver Signale (👍, ≥ 70 % geschaut) minus negativer Signale (👎, ausgeblendet, nach < 10 % abgebrochen); ältere Signale verlieren über eine Halbwertszeit an Gewicht
- Feed-Sortierung wählbar: „Neueste“ / „Für mich“ (Mischung aus Relevanz und Aktualität)
- Optionaler Schwellwert „Wahrscheinlich uninteressant ausblenden“ – solche Videos landen in „Ausgeblendet“ mit Grund „Automatisch (Score x)“
- **LLM-Modul (optional, `LLM_ENABLED`)**:
  - OpenAI-kompatibler Client mit `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (getestet gegen OpenRouter- und Ollama-artige Endpunkte, im Test gemockt)
  - Freitext-Interessenbeschreibung pro Nutzer („Was interessiert mich, was nicht?“)
  - Nachbewertung der Top-Kandidaten mit kurzer Begründung („Warum sehe ich das?“), Ergebnisse gecacht
  - Robust: Timeouts, Retries mit Backoff, bei Fehlern Fallback auf reines Embedding-Ranking
  - Aufruf- und Kostenzähler (pro Nutzer) auf der Admin-Statusseite
- Kaltstart: optionaler Import von `watch-history.json` aus Google Takeout pro Nutzer
- Optional mit LLM: kurze Zusammenfassung eines Videos aus heruntergeladenen Untertiteln (nur wenn Untertitel aus dem Download-Modul vorliegen; die offizielle API liefert Untertitel fremder Videos nicht)

**Abnahme:**
- [ ] kurator funktioniert unverändert mit `LLM_ENABLED=false`
- [ ] Wechsel zwischen OpenRouter und lokalem Endpunkt nur per `.env`
- [ ] Profile verschiedener Nutzer beeinflussen sich nicht gegenseitig (Test)
- [ ] Jede automatische Ausblendung ist begründet und rückgängig zu machen
- [ ] Tests für Profilberechnung und Ranking mit festen Testvektoren

---

## Phase 4 – Entdecken

**Ziel:** Neue, passende Videos und Kanäle außerhalb der Abos finden – innerhalb eines festen, fair verteilten Quota-Budgets.

**Kandidatenquellen** (jede einzeln abschaltbar):
1. Vorgestellte Kanäle der Abos (`channelSections.list`, 1 Einheit)
2. In Beschreibungen geschauter Videos erwähnte Kanäle (`@handles`, Kanal-Links)
3. Suchanfragen, abgeleitet aus dem Interessenprofil (per LLM, falls aktiv, sonst aus häufigen Schlagwörtern positiv bewerteter Videos); strikt begrenzt durch `QUOTA_SEARCH_BUDGET`, fair auf aktive Nutzer verteilt, in nächtlichen Läufen
4. Weitere Videos von Kanälen, aus denen einzelne Videos gut ankamen

**Ablauf:** Kandidaten sammeln → Shorts/Filterregeln anwenden → Embedding-Score → optional LLM-Nachbewertung → Top-N pro Nutzer speichern. Ca. 10 % Erkundungsanteil aus weniger ähnlichen Themen.

**UI:**
- Seite „Entdecken“ mit Videos und vorgeschlagenen Kanälen inkl. Quelle und Begründung
- Aktionen: abonnieren, ansehen, ausblenden, „mehr davon“ / „weniger davon“
- Abgelehnte Kanalvorschläge kommen nicht wieder

**Abnahme:**
- [ ] Tägliches Quota-Budget wird nachweislich nie überschritten (Test + Statusseite)
- [ ] Empfehlungen enthalten keine bereits abonnierten Kanäle, keine Shorts, keine ausgeblendeten Videos
- [ ] Jede Quelle einzeln deaktivierbar

---

## Phase 5 – Komfort (Auswahl nach Bedarf)

- SponsorBlock-Integration (Sponsorsegmente überspringen, pro Nutzer abschaltbar; bei Downloads optional herausschneiden)
- DeArrow-Integration (neutralere Titel/Thumbnails statt Clickbait, pro Nutzer abschaltbar)
- Suche innerhalb der bereits gespeicherten Videos (lokal, ohne Quota); YouTube-Suche optional mit Budget
- „Später ansehen“-Liste mit Sortierung, als Abspielliste mit automatischem Weiterspielen
- Kanalgruppen/Tags (z. B. „Wissen“, „Musik“) mit Feed-Filter nach Gruppe
- Optional: Tagesbudget pro Nutzer (Anzahl Videos oder Minuten) und Kinderprofile mit Zeitlimit
- Export/Import der eigenen Daten (JSON) pro Nutzer, Backup-Download für Admins
- Benachrichtigung (z. B. ntfy/Gotify) bei neuen Videos ausgewählter Kanäle

Bewusst **nicht** übernommen aus ähnlichen Projekten: Scraping von YouTube-Seiten (z. B. für Videolängen), User-Agent-Tricks für RSS, ein eigener Shorts-Bereich.

---

## Phase 6 – Mobil (später)

- Versionierte JSON-API (`/api/v1`) auf Basis der bestehenden Service-Module, Token-Authentifizierung pro Gerät (in der UI erzeug- und widerrufbar)
- Handy-taugliche Oberfläche bzw. installierbare PWA als erster Schritt
- Danach Entscheidung: native App oder PWA ausreichend
- Zugriff weiterhin nur über Heimnetz/VPN
