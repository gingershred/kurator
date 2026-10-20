# Installation & Betrieb

> **⚠️ kurator nur im Heimnetz oder über ein VPN betreiben.**
> kurator ist für den Einsatz in einem vertrauenswürdigen Netz gebaut (kein 2FA, keine
> Härtung gegen Angriffe aus dem Internet). Stelle es **nicht** öffentlich ins Internet –
> weder per Portfreigabe noch über einen öffentlich erreichbaren Reverse Proxy. Für den
> Zugriff von unterwegs eignet sich ein VPN wie WireGuard oder Tailscale.

## Voraussetzungen

- Linux-Host (amd64 oder arm64, z. B. Raspberry Pi 4/5) mit **Docker** und dem
  **Docker-Compose-Plugin** (`docker compose version`)
- Optional: ein YouTube-Data-API-Schlüssel (empfohlen, siehe unten)

## Erster Start

```bash
mkdir -p /opt/kurator && cd /opt/kurator

# Compose-Datei und Beispielkonfiguration holen
curl -fsSLO https://raw.githubusercontent.com/gingershred/kurator/main/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/gingershred/kurator/main/.env.example -o .env

# Datenverzeichnis anlegen – der Container läuft als UID/GID 1000
mkdir -p data && sudo chown 1000:1000 data
```

`.env` bearbeiten – mindestens:

```env
KURATOR_SECRET_KEY=...        # z. B. python3 -c "import secrets; print(secrets.token_urlsafe(48))"
KURATOR_BASE_URL=http://kurator.lan:8080
KURATOR_ADMIN_USERNAME=admin
KURATOR_ADMIN_PASSWORD=...    # mind. 10 Zeichen, nur für den ersten Start
```

Starten:

```bash
docker compose pull
docker compose up -d
docker compose ps        # web und worker sollten nach kurzer Zeit "healthy" sein
```

kurator ist jetzt unter `http://<host>:8080` erreichbar. Beim ersten Start wird die
Datenbank angelegt und der Admin aus `KURATOR_ADMIN_USERNAME`/`KURATOR_ADMIN_PASSWORD`
erzeugt – aber nur, solange noch kein Nutzer existiert. **Entferne danach
`KURATOR_ADMIN_PASSWORD` aus der `.env`** und starte neu (`docker compose up -d`).

Alle Einstellungen sind in [`.env.example`](../.env.example) kommentiert.

### Weitere Nutzer

Es gibt keine öffentliche Registrierung. Nutzer legt der Admin an:

- in der Oberfläche unter **Nutzer** (Rolle, Passwort, Deaktivieren, Löschen), oder
- per CLI:

  ```bash
  docker compose exec web python -m app.cli create-user erika          # fragt das Passwort ab
  docker compose exec web python -m app.cli create-user chef --admin
  docker compose exec web python -m app.cli reset-password erika
  ```

### Kanäle hinzufügen

Unter **Kanäle** kann jeder Nutzer Kanäle abonnieren:

- per Kanal-ID (`UC…`), Kanal-Link (`https://www.youtube.com/channel/UC…`) oder
  `@Handle` bzw. `https://www.youtube.com/@…` (Handles brauchen den API-Schlüssel),
- per **Google-Takeout-Import**: auf [takeout.google.com](https://takeout.google.com) nur
  „YouTube und YouTube Music“ auswählen, unter den Optionen nur „Abos“ exportieren und die
  Datei `subscriptions.csv` hochladen. Der Import kostet keine API-Einheiten, auch bei
  Hunderten Kanälen. Alternativ per CLI:

  ```bash
  cp subscriptions.csv data/ && sudo chown 1000:1000 data/subscriptions.csv
  docker compose exec web python -m app.cli import-takeout erika /data/subscriptions.csv
  ```

- per **OPML** aus einem RSS-Reader (z. B. FreshRSS, Miniflux) oder einem anderen
  YouTube-Feed-Tool – über dasselbe Upload-Feld oder `python -m app.cli import-opml`.

Unter **Kanäle → Als OPML exportieren** bekommst du deine Abos als OPML-Datei. Sie enthält
die Feeds der Upload-Playlist ohne Shorts, ist also auch in anderen Readern Shorts-frei.

Neue Kanäle werden sofort im Hintergrund abgerufen, danach alle `FEED_POLL_MINUTES`
Minuten (Standard 30). Kanäle werden zwischen Nutzern geteilt und nur einmal abgerufen.

### YouTube-API-Schlüssel (empfohlen)

Ohne Schlüssel nutzt kurator nur die kostenlosen RSS-Feeds der offiziellen
Upload-Playlist ohne Shorts. Mit Schlüssel kommen Videolänge, Tags und Live-/Premierenstatus
hinzu – damit greifen Längenfilter, Dauer-Regeln, das Ausblenden von Livestreams/Premieren
und der zusätzliche Shorts-Filter (alles bis `SHORTS_MAX_SECONDS`). Mit Schlüssel erscheint
ein Video erst im Feed, wenn seine Länge bekannt ist. Schlüssel anlegen in der
[Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
→ „YouTube Data API v3“ aktivieren → Anmeldedaten → API-Schlüssel (am besten auf die
YouTube Data API beschränken) → als `YOUTUBE_API_KEY` in die `.env`. Das Tageskontingent
(10.000 Einheiten) teilen sich alle Nutzer; kurator bleibt mit `QUOTA_DAILY_BUDGET`
darunter. Der Verbrauch steht auf der Admin-Seite **Status**.

**Ersatzabruf bei RSS-Störungen:** YouTubes RSS-Endpunkt liefert seit Ende 2025 zeitweise
Fehler. Mit Schlüssel lädt kurator betroffene Kanäle dann über die offizielle API nach
(`playlistItems.list`, 1 Einheit pro Kanal). Dafür werden höchstens 25 % des Tagesbudgets
genutzt; bei vielen Kanälen werden sie dann seltener nachgeladen, das Budget für alles andere
bleibt unangetastet. Ohne Schlüssel bleiben gestörte Kanäle bis zur Erholung des Feeds
stehen; die Seite **Status** zeigt den Zustand des letzten Abrufs.

### Privatsphäre

- Thumbnails lädt der Worker einmal herunter (`data/thumbs/`); beim Durchblättern des
  Feeds spricht dein Browser nicht mit Google.
- Abgespielt wird über den eingebetteten Player von `youtube-nocookie.com`. Erst beim
  Öffnen eines Videos lädt der Browser Inhalte von YouTube.
- Ein Google-Konto ist nicht nötig.

### Bedienung

Tastaturkürzel im Feed: `j`/`k` nächstes/vorheriges Video, `Enter` öffnen, `w` gesehen,
`h` ausblenden, `l` später ansehen, `+`/`-` bewerten, `?` Hilfe. Ab 90 % Wiedergabe gilt ein
Video als gesehen; beim nächsten Öffnen geht es an der letzten Stelle weiter.

## Updates

kurator-Updates bestehen nur aus einem neuen Image; alle Daten liegen in `./data`.
Beim Start sichert kurator die Datenbank automatisch nach `data/backups/` (die letzten 10
bleiben erhalten), **bevor** es Datenbank-Migrationen ausführt.

Manuell:

```bash
cd /opt/kurator
docker compose pull && docker compose up -d
```

Automatisch per Cron (z. B. täglich um 4:30 Uhr):

```cron
30 4 * * * cd /opt/kurator && docker compose pull -q && docker compose up -d && docker image prune -f
```

Image-Tags auf `ghcr.io/gingershred/kurator`:

| Tag | Bedeutung |
|---|---|
| `latest` | aktueller Stand von `main` (wird zusätzlich wöchentlich neu gebaut) |
| `1.2.3`, `1.2` | feste Versionen bzw. neueste Patch-Version einer Reihe |
| `sha-abc1234` | ein bestimmter Commit |

Wer Updates kontrollierter möchte, trägt in `docker-compose.yml` statt `latest` eine
Version wie `1.2` ein.

## Backup & Wiederherstellung

Alles Wichtige liegt in `./data` (Datenbank `kurator.db`, Backups, später Modelle und
Downloads).

**Backup im laufenden Betrieb** (konsistente Kopie der Datenbank nach `data/backups/`):

```bash
docker compose exec web python -m app.cli backup
```

Beachte: In `data/backups/` werden immer nur die 10 neuesten Sicherungen behalten. Für
langfristige Backups das Verzeichnis `data/` (oder einzelne Dateien aus `data/backups/`)
regelmäßig auf ein anderes Gerät kopieren, z. B. mit restic, borg oder rsync.

**Vollständiges Backup** (inkl. aller Dateien):

```bash
docker compose stop
tar czf kurator-backup-$(date +%F).tar.gz data .env
docker compose start
```

**Wiederherstellen** einer Datenbank-Sicherung:

```bash
docker compose down
cp data/kurator.db data/kurator.db.kaputt            # aktuellen Stand aufheben
cp data/backups/kurator-<zeitstempel>-<...>.db data/kurator.db
rm -f data/kurator.db-wal data/kurator.db-shm
sudo chown 1000:1000 data/kurator.db
docker compose up -d
```

Ist die Sicherung älter als das Image, migriert kurator sie beim Start automatisch.

## HTTPS / Reverse Proxy (im LAN)

Läuft kurator hinter einem Reverse Proxy mit HTTPS (z. B. Caddy, Traefik, nginx im
Heimnetz), setze:

```env
KURATOR_BASE_URL=https://kurator.lan
KURATOR_SECURE_COOKIES=true
```

## Single Sign-on (OIDC, optional)

kurator kann sich an einen OIDC-Anbieter wie Authentik, Authelia oder Keycloak anbinden.

1. Beim Anbieter einen „confidential“ OIDC-Client anlegen, Redirect-URI:
   `<KURATOR_BASE_URL>/auth/oidc/callback`, Scopes `openid profile email`.
2. In der `.env`:

   ```env
   KURATOR_AUTH_MODE=both          # oder oidc (dann ohne Passwort-Login)
   OIDC_ISSUER=https://auth.example.lan/application/o/kurator/
   OIDC_CLIENT_ID=...
   OIDC_CLIENT_SECRET=...
   OIDC_USERNAME_CLAIM=preferred_username
   ```

3. Der Admin legt jeden Nutzer zuerst in kurator an (das Passwort darf leer bleiben). Beim
   ersten SSO-Login wird der Wert aus `OIDC_USERNAME_CLAIM` mit dem kurator-Benutzernamen
   verglichen und das Konto fest verknüpft; danach zählt nur noch die OIDC-ID (`sub`).
   Unbekannte Konten werden abgewiesen. Die Verknüpfung lässt sich in der
   Nutzerverwaltung wieder lösen.

Tipp: Bei `KURATOR_AUTH_MODE=oidc` sollte der erste Admin vor der Umstellung im
OIDC-Anbieter denselben Benutzernamen haben, sonst sperrst du dich aus. Notfalls hilft
`KURATOR_AUTH_MODE=both` und `python -m app.cli reset-password <name>`.

## Fehlersuche

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f worker
```

- **„Das Datenverzeichnis /data ist nicht beschreibbar“** – `sudo chown -R 1000:1000 data`
- **Worker „unhealthy“** – Logs des Workers prüfen; die Admin-Seite **Status** zeigt das
  letzte Lebenszeichen und fehlgeschlagene Jobs.
- **Zu viele Fehlversuche beim Login** – nach 5 falschen Passwörtern ist die Anmeldung für
  15 Minuten gesperrt (oder bis zum Neustart von `web`).
- **Passwort vergessen** – `docker compose exec web python -m app.cli reset-password <name>`
- **Kanäle mit Fehlern / „RSS gestört“** – meist eine vorübergehende Störung bei YouTube.
  Mit API-Schlüssel springt der Ersatzabruf ein. Kanäle, die laut RSS und API nicht mehr
  existieren, werden als „existiert nicht mehr“ markiert und nur noch wöchentlich geprüft.
