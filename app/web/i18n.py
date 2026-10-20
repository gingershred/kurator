"""Central collection of all UI texts (German), so they can be translated later."""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "de"

MESSAGES: dict[str, dict[str, str]] = {
    "de": {
        # General
        "app.tagline": "Dein ruhiger Abo-Feed",
        "nav.account": "Konto",
        "nav.admin_users": "Nutzer",
        "nav.admin_status": "Status",
        "nav.logout": "Abmelden",
        "footer.license": "Freie Software unter der GNU AGPLv3",
        "footer.source": "Quellcode",
        "common.save": "Speichern",
        "common.cancel": "Abbrechen",
        "common.back": "Zurück",
        "common.yes": "Ja",
        "common.no": "Nein",
        "common.never": "nie",
        "common.none": "–",
        "common.close": "Schließen",
        "shortcuts.title": "Tastaturkürzel",
        "shortcuts.hint": "Taste ? zeigt die Tastaturkürzel",
        "shortcuts.nav": "nächstes / vorheriges Video",
        "shortcuts.open": "Video öffnen",
        "shortcuts.watched": "gesehen / ungesehen",
        "shortcuts.hide": "ausblenden / einblenden",
        "shortcuts.later": "später ansehen",
        "shortcuts.rate": "gefällt mir / gefällt mir nicht",
        "shortcuts.help": "diese Hilfe",
        # Login
        "login.title": "Anmelden",
        "login.username": "Benutzername",
        "login.password": "Passwort",
        "login.submit": "Anmelden",
        "login.oidc": "Mit Single Sign-on anmelden",
        "login.or": "oder",
        "login.failed": "Benutzername oder Passwort ist falsch.",
        "login.rate_limited": "Zu viele Fehlversuche. Bitte in {minutes} Minuten erneut versuchen.",
        "login.logged_out": "Du wurdest abgemeldet.",
        "login.oidc_failed": "Die Anmeldung per Single Sign-on ist fehlgeschlagen.",
        "login.oidc_unknown_user": (
            "Für dieses Single-Sign-on-Konto gibt es keinen kurator-Nutzer. "
            "Bitte wende dich an den Admin."
        ),
        # Channels
        "nav.channels": "Kanäle",
        "channels.title": "Kanäle",
        "channels.add": "Kanal hinzufügen",
        "channels.input": "Kanal",
        "channels.add_button": "Hinzufügen",
        "channels.no_api_key_hint": (
            "Ohne YouTube-API-Schlüssel funktionieren nur Kanal-IDs (UC…), Links mit /channel/ "
            "und der Takeout-Import. @Handles brauchen den Schlüssel."
        ),
        "channels.import": "Aus Google Takeout importieren",
        "channels.import_hint": (
            "Unter takeout.google.com „YouTube und YouTube Music“ → „Abos“ exportieren und die "
            "Datei subscriptions.csv hier hochladen."
        ),
        "channels.import_button": "Importieren",
        "channels.imported": (
            "Import abgeschlossen: {added} neu, {existing} bereits abonniert, "
            "{invalid} ungültige Zeilen. Die Videos werden im Hintergrund geladen."
        ),
        "channels.added": "„{title}“ wurde abonniert. Die Videos werden im Hintergrund geladen.",
        "channels.removed": "„{title}“ wurde entfernt.",
        "channels.list": "Deine Abos ({count})",
        "channels.name": "Kanal",
        "channels.status": "Status",
        "channels.status_active": "aktiv",
        "channels.status_muted": "stummgeschaltet",
        "channels.last_fetch": "Zuletzt abgerufen",
        "channels.error": "Fehler",
        "channels.error_count": "{count}× fehlgeschlagen",
        "channels.gone": "existiert nicht mehr",
        "channels.mute": "Stummschalten",
        "channels.unmute": "Aktivieren",
        "channels.remove": "Entfernen",
        "channels.empty": "Du hast noch keine Kanäle abonniert.",
        # Settings & rules
        "nav.settings": "Einstellungen",
        "settings.title": "Einstellungen",
        "settings.feed": "Feed",
        "settings.show_livestreams": "Livestreams anzeigen",
        "settings.show_premieres": "Premieren und angekündigte Livestreams anzeigen",
        "settings.saved": "Einstellungen gespeichert.",
        "settings.rules": "Filterregeln",
        "settings.rules_hint": (
            "Videos, auf die eine aktive Regel zutrifft, landen unter „Ausgeblendet“. "
            "Deaktivieren oder Löschen macht das sofort rückgängig."
        ),
        "settings.rule": "Regel",
        "settings.rule_new": "Neue Regel",
        "settings.rule_type": "Art",
        "settings.rule_value": "Wert",
        "settings.rule_channel": "Kanal (nur für „Kanal ausschließen“)",
        "settings.rule_value_hint": (
            "Stichwörter ohne Beachtung von Groß-/Kleinschreibung; Dauer in Minuten; "
            "Regex nach Python-Syntax, durchsucht Titel und Beschreibung."
        ),
        "settings.rule_add": "Regel hinzufügen",
        "settings.rule_added": "Regel hinzugefügt.",
        "settings.rule_deleted": "Regel gelöscht.",
        "settings.rule_enable": "Aktivieren",
        "settings.rule_disable": "Deaktivieren",
        "settings.rule_delete": "Löschen",
        "rule.type.keyword_title": "Stichwort im Titel",
        "rule.type.keyword_desc": "Stichwort in der Beschreibung",
        "rule.type.channel": "Kanal ausschließen",
        "rule.type.min_duration": "Mindestdauer (Minuten)",
        "rule.type.max_duration": "Höchstdauer (Minuten)",
        "rule.type.regex": "Regulärer Ausdruck",
        "rule.label.keyword_title": "Stichwort „{value}“ im Titel",
        "rule.label.keyword_desc": "Stichwort „{value}“ in der Beschreibung",
        "rule.label.channel": "Kanal „{value}“",
        "rule.label.min_duration": "kürzer als {minutes} min",
        "rule.label.max_duration": "länger als {minutes} min",
        "rule.label.regex": "Regex /{value}/",
        # Feed
        "nav.feed": "Feed",
        "nav.later": "Später",
        "nav.hidden": "Ausgeblendet",
        "feed.title_feed": "Feed",
        "feed.title_later": "Später ansehen",
        "feed.title_hidden": "Ausgeblendet",
        "feed.filter_show": "Anzeigen",
        "feed.unwatched": "Ungesehen",
        "feed.all": "Alle",
        "feed.all_channels": "Alle Kanäle",
        "feed.length": "Länge",
        "feed.length_any": "Beliebig",
        "feed.length_short": "unter 20 min",
        "feed.length_medium": "20–60 min",
        "feed.length_long": "über 60 min",
        "feed.apply": "Filtern",
        "feed.more": "Mehr laden",
        "feed.reason": "Grund",
        "feed.no_subscriptions": "Du hast noch keine Kanäle abonniert.",
        "feed.add_channels": "Kanäle hinzufügen",
        "feed.empty_feed": "Keine neuen Videos. Schön ruhig hier.",
        "feed.empty_later": "Deine „Später ansehen“-Liste ist leer.",
        "feed.empty_hidden": "Nichts ausgeblendet.",
        "feed.no_api_key": (
            "Ohne YouTube-API-Schlüssel kennt kurator keine Videolängen. Shorts werden trotzdem "
            "über die offizielle Playlist ohne Shorts ferngehalten; Längenfilter und "
            "Dauer-Regeln greifen aber nicht."
        ),
        "reason.manual": "manuell ausgeblendet",
        "reason.muted": "Kanal stummgeschaltet",
        "reason.live": "Livestream",
        "reason.premiere": "Premiere bzw. angekündigter Livestream",
        "reason.rule": "Regel",
        "video.resume": "weiter bei {position}",
        "video.open_youtube": "Auf YouTube öffnen",
        "video.description": "Beschreibung",
        "video.live": "LIVE",
        "video.upcoming": "BALD",
        "action.watched": "Gesehen",
        "action.watched_state": "Gesehen",
        "action.unwatched": "Als ungesehen markieren",
        "action.later": "Später",
        "action.later_state": "Später",
        "action.unlater": "Von „Später“ entfernen",
        "action.like": "Gefällt mir",
        "action.dislike": "Gefällt mir nicht",
        "action.hide": "Ausblenden",
        "action.unhide": "Einblenden",
        "action.disable_rule": "Regel deaktivieren",
        "time.minutes": "vor {n} min",
        "time.hours": "vor {n} Std.",
        "time.days": "vor {n} Tagen",
        "time.months": "vor {n} Monaten",
        "time.years": "vor {n} Jahren",
        # Account
        "account.title": "Mein Konto",
        "account.change_password": "Passwort ändern",
        "account.current_password": "Aktuelles Passwort",
        "account.new_password": "Neues Passwort",
        "account.new_password_repeat": "Neues Passwort wiederholen",
        "account.password_changed": "Dein Passwort wurde geändert.",
        "account.no_local_password": (
            "Dein Konto meldet sich per Single Sign-on an und hat kein lokales Passwort."
        ),
        "account.role": "Rolle",
        # Admin – users
        "admin.users.title": "Nutzerverwaltung",
        "admin.users.new": "Neuer Nutzer",
        "admin.users.username": "Benutzername",
        "admin.users.role": "Rolle",
        "admin.users.active": "Aktiv",
        "admin.users.oidc": "Single Sign-on",
        "admin.users.last_login": "Letzter Login",
        "admin.users.created": "Angelegt",
        "admin.users.actions": "Aktionen",
        "admin.users.edit": "Bearbeiten",
        "admin.users.password": "Passwort",
        "admin.users.password_hint": (
            "Mindestens 10 Zeichen. Leer lassen für reine Single-Sign-on-Nutzer."
        ),
        "admin.users.create": "Nutzer anlegen",
        "admin.users.created_ok": "Nutzer „{username}“ wurde angelegt.",
        "admin.users.edit_title": "Nutzer „{username}“ bearbeiten",
        "admin.users.save_role": "Rolle speichern",
        "admin.users.role_saved": "Rolle wurde geändert.",
        "admin.users.reset_password": "Passwort zurücksetzen",
        "admin.users.password_reset_ok": (
            "Das Passwort wurde gesetzt. Bestehende Sitzungen des Nutzers wurden beendet."
        ),
        "admin.users.deactivate": "Deaktivieren",
        "admin.users.activate": "Aktivieren",
        "admin.users.deactivated": "Nutzer wurde deaktiviert.",
        "admin.users.activated": "Nutzer wurde aktiviert.",
        "admin.users.unlink_oidc": "Single-Sign-on-Verknüpfung lösen",
        "admin.users.oidc_unlinked": "Die Single-Sign-on-Verknüpfung wurde gelöst.",
        "admin.users.oidc_linked": "verknüpft",
        "admin.users.oidc_not_linked": "nicht verknüpft",
        "admin.users.delete": "Nutzer löschen",
        "admin.users.delete_warning": (
            "Löscht den Nutzer und alle seine Daten (Abos, Verlauf, Bewertungen, Einstellungen) "
            "unwiderruflich."
        ),
        "admin.users.delete_confirm": "Benutzernamen zur Bestätigung eingeben",
        "admin.users.deleted": "Nutzer „{username}“ wurde gelöscht.",
        "role.admin": "Admin",
        "role.user": "Nutzer",
        # Admin – status
        "admin.status.title": "Status",
        "admin.status.version": "Version",
        "admin.status.db_revision": "Datenbank-Stand",
        "admin.status.db_head": "aktuell",
        "admin.status.db_outdated": "veraltet (erwartet {head})",
        "admin.status.db_size": "Datenbankgröße",
        "admin.status.users": "Nutzer",
        "admin.status.auth_mode": "Anmeldung",
        "admin.status.api_key": "YouTube-API-Schlüssel",
        "admin.status.api_key_set": "konfiguriert",
        "admin.status.api_key_missing": "nicht gesetzt – nur RSS-Daten",
        "admin.status.quota": "Quota heute",
        "admin.status.quota_value": "{used} von {budget} Einheiten (davon Suche: {search})",
        "admin.status.rss": "Feed-Abruf (letzter Lauf)",
        "admin.status.rss_value": (
            "{ok} von {channels} Kanälen ok, davon {fallback} per API nachgeladen; "
            "{failed} fehlgeschlagen"
        ),
        "admin.status.rss_degraded": "RSS gestört",
        "admin.status.rss_degraded_no_key": (
            "Mit einem YouTube-API-Schlüssel lädt kurator betroffene Kanäle über die "
            "offizielle API nach."
        ),
        "admin.status.gone_channels": "{count} Kanal/Kanäle existieren nicht mehr",
        "admin.status.fallback": "API-Ersatzabruf heute",
        "admin.status.fallback_value": "{used} von höchstens {budget} Einheiten",
        "admin.status.worker": "Worker",
        "admin.status.worker_alive": "aktiv (letztes Lebenszeichen {when})",
        "admin.status.worker_dead": "kein Lebenszeichen seit {when}",
        "admin.status.worker_unknown": "noch kein Lebenszeichen",
        "admin.status.queue": "Warteschlange",
        "admin.status.queue_value": "{queued} wartend, {running} laufend, {failed} fehlgeschlagen",
        "admin.status.recent_runs": "Letzte Jobs",
        "admin.status.run_name": "Job",
        "admin.status.run_started": "Gestartet",
        "admin.status.run_duration": "Dauer",
        "admin.status.run_status": "Status",
        "admin.status.run_error": "Fehler",
        "admin.status.no_runs": "Noch keine Jobs gelaufen.",
        "run.running": "läuft",
        "run.success": "ok",
        "run.failed": "Fehler",
        # Errors
        "error.title": "Fehler",
        "error.403": "Dafür fehlen dir die Rechte.",
        "error.404": "Diese Seite gibt es nicht.",
        "error.generic": "Etwas ist schiefgelaufen.",
        "error.csrf": (
            "Das Formular ist abgelaufen. Bitte lade die Seite neu und versuche es erneut."
        ),
        "error.username_invalid": (
            "Benutzernamen: 2–64 Zeichen, nur Buchstaben a–z, Ziffern, Punkt, "
            "Unterstrich und Bindestrich."
        ),
        "error.username_taken": "Dieser Benutzername ist bereits vergeben.",
        "error.password_too_short": "Das Passwort muss mindestens 10 Zeichen lang sein.",
        "error.password_too_long": "Das Passwort ist zu lang.",
        "error.password_mismatch": "Die Passwörter stimmen nicht überein.",
        "error.current_password_wrong": "Das aktuelle Passwort ist falsch.",
        "error.last_admin": "Der letzte aktive Admin kann nicht entfernt oder herabgestuft werden.",
        "error.cannot_delete_self": "Du kannst dich nicht selbst löschen.",
        "error.cannot_deactivate_self": "Du kannst dich nicht selbst deaktivieren.",
        "error.delete_confirm_mismatch": "Der eingegebene Benutzername stimmt nicht überein.",
        "error.local_login_disabled": "Die Anmeldung mit Passwort ist deaktiviert.",
        "error.channel_needs_api_key": (
            "@Handles und Nutzernamen lassen sich nur mit YouTube-API-Schlüssel auflösen. "
            "Bitte die Kanal-ID (UC…) oder den /channel/-Link verwenden."
        ),
        "error.channel_not_found": "Diesen Kanal gibt es auf YouTube nicht.",
        "error.channel_already_subscribed": "Diesen Kanal hast du bereits abonniert.",
        "error.quota_exhausted": (
            "Das tägliche YouTube-Kontingent ist aufgebraucht. Bitte morgen erneut versuchen "
            "oder die Kanal-ID verwenden."
        ),
        "error.youtube_api": (
            "Die YouTube-API hat einen Fehler gemeldet. Bitte später erneut versuchen."
        ),
        "error.import_too_large": "Die Datei ist zu groß (höchstens 2 MB).",
        "error.import_empty": "In der Datei wurden keine Kanäle gefunden.",
        "error.rule_type_invalid": "Unbekannte Regelart.",
        "error.rule_value_empty": "Bitte einen Wert angeben.",
        "error.rule_value_too_long": "Der Wert ist zu lang (höchstens 200 Zeichen).",
        "error.rule_regex_invalid": "Der reguläre Ausdruck ist ungültig.",
        "error.rule_duration_invalid": "Bitte eine Dauer in ganzen Minuten (1–1440) angeben.",
        "error.rule_channel_unknown": "Diesen Kanal hast du nicht abonniert.",
        "error.channel_input_invalid": (
            "Das sieht nicht nach einem Kanal aus. Erlaubt sind Kanal-ID (UC…), @Handle oder "
            "eine Kanal-URL."
        ),
        "error.channel_input_custom_url": (
            "Alte /c/…-Adressen lassen sich nicht offiziell auflösen. Bitte das @Handle oder "
            "die Kanal-URL mit /channel/UC… verwenden."
        ),
        "error.channel_input_video_url": (
            "Das ist ein Video-Link. Bitte den Link zum Kanal angeben."
        ),
    }
}


def translate(key: str, language: str = DEFAULT_LANGUAGE, **kwargs: Any) -> str:
    messages = MESSAGES.get(language) or MESSAGES[DEFAULT_LANGUAGE]
    text = messages.get(key) or MESSAGES[DEFAULT_LANGUAGE].get(key) or key
    return text.format(**kwargs) if kwargs else text
