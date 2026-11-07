# TikTok-Bot – „Was passiert, wenn…?“

Erstellt jeden Tag automatisch 2 deutsche Simulationsvideos (1080x1920) und legt sie als GitHub-Release ab.
Eine geplante Claude-Aufgabe plant sie danach über Metricool auf TikTok ein (12:30 und 19:00 Uhr, als KI-Inhalt gekennzeichnet).

- `data/topics.json` – Themen und Skripte
- `data/state.json` – bereits verwendete Themen
- `bot/render.py` – Stimme (edge-tts), KI-Bilder (Pollinations), Bewegung, Untertitel
- `queue/` – fertige Videos pro Tag mit Link und Beschreibung
