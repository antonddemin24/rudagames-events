# Rudagames Events → ICS

Парсер мероприятий со страницы [rudagames.com/helsinki](https://rudagames.com/helsinki),
генерирующий `events.ics` для подписки в Google/Apple/Outlook Calendar.

## Установка

### С venv

```bash
python3 -m venv .venv
source .venv/bin/activate   # или .venv\Scripts\activate в Windows
pip install -r requirements.txt
playwright install chromium
```
