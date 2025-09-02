# rudagames_to_ics_playwright.py
# -*- coding: utf-8 -*-

import os
import re
import time
import hashlib
import datetime as dt
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from ics import Calendar, Event
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# --------------------
# Конфигурация
# --------------------
URL = os.getenv("RUDA_URL", "https://rudagames.com/helsinki")
TZ = ZoneInfo("Europe/Helsinki")
ICS_PATH = os.getenv("ICS_PATH", "events.ics")
CLICK_LIMIT = int(os.getenv("CLICK_LIMIT", "30"))
WAIT_AFTER_CLICK_MS = int(os.getenv("WAIT_AFTER_CLICK_MS", "700"))
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "60000"))
HEADFUL_FIRST_TRY = os.getenv("HEADFUL", "1") not in ("0", "false", "False")
DEBUG_HTML = os.getenv("DEBUG_HTML", "1") not in ("0", "false", "False")

# Русские месяцы → номер
RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
}

# Примеры: "08 сентября пн, 18:30", "03 октября пт, 19:00"
DATE_RX = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>\w+)\s+\w+,\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})",
    re.IGNORECASE
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# --------------------
# Вспомогательные функции
# --------------------
def stable_uid(*parts) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() + "@rudagames"

def parse_datetime_ru(text: str, year: int | None = None) -> dt.datetime | None:
    m = DATE_RX.search(text.strip())
    if not m:
        return None
    day = int(m.group("day"))
    month_name = m.group("month").lower()
    hour = int(m.group("hour"))
    minute = int(m.group("minute"))
    month = RU_MONTHS.get(month_name)
    if not month:
        return None

    now = dt.datetime.now(TZ)
    yr = year or now.year
    candidate = dt.datetime(yr, month, day, hour, minute, tzinfo=TZ)
    # Если дата уже сильно в прошлом, вероятно, речь о следующем годе
    if candidate < now - dt.timedelta(days=60):
        candidate = candidate.replace(year=yr + 1)
    return candidate

def extract_events(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Карточка: div с классом 'bg-newGradient' (нижняя часть карточки с текстом)
    card_divs = [d for d in soup.find_all("div") if "bg-newGradient" in (d.get("class") or [])]

    seen = set()
    debug_samples = 0

    for card in card_divs:
        # Текст карточки для быстрых поисков
        full_text = card.get_text(" ", strip=True)

        # Дата/время
        dt_match = DATE_RX.search(full_text)
        date_text = dt_match.group(0) if dt_match else None

        # Заголовок — p с жирным шрифтом (в разметке он действительно жирный)
        title = None
        for p in card.find_all("p"):
            classes = p.get("class") or []
            t = p.get_text(strip=True)
            if not t:
                continue
            # отбрасываем строки с ценой/временем
            if "EUR" in t or DATE_RX.search(t):
                continue
            if "font-bold" in classes or "font-extrabold" in classes:
                title = t
                break
        # Резерв: самый длинный p без даты и EUR
        if not title:
            candidates = []
            for p in card.find_all("p"):
                t = p.get_text(strip=True)
                if t and "EUR" not in t and not DATE_RX.search(t):
                    candidates.append(t)
            if candidates:
                title = max(candidates, key=len)

        # Площадка — ищем p с характерными словами
        venue = None
        for p in card.find_all("p"):
            t = p.get_text(strip=True)
            if re.search(r"(bar|ravintola|pub|restaurant|wäiski|draft|sports)", t, re.I):
                venue = t
                break

        # Цена — ищем «число + EUR» по всему тексту карточки
        price = None
        m_price = re.search(r"(\d+)\s*EUR", full_text, re.I)
        if m_price:
            price = f"{m_price.group(1)} EUR / чел" if "чел" in full_text.lower() else f"{m_price.group(1)} EUR"

        # Ссылка (если есть внутри карточки)
        href = None
        a = card.find("a", href=True)
        if a:
            href = a["href"]

        if DEBUG_HTML and debug_samples < 3:
            # Печатаем несколько примеров распознавания
            print("[debug] card text:", full_text[:160], "…")
            print("[debug]  title:", title, "| venue:", venue, "| date:", date_text, "| price:", price)
            debug_samples += 1

        if not (title and date_text):
            continue

        start = parse_datetime_ru(date_text)
        if not start:
            continue

        end = start + dt.timedelta(hours=2)  # дефолтная длительность
        uid = stable_uid(title, start.isoformat(), venue or "", href or "")

        if uid in seen:
            continue
        seen.add(uid)

        items.append({
            "uid": uid,
            "title": title,
            "start": start,
            "end": end,
            "venue": venue,
            "price": price,
            "url": href
        })

    # Сортировка и фильтр на будущее
    items.sort(key=lambda e: e["start"])
    now = dt.datetime.now(TZ)
    items = [e for e in items if e["start"] >= now - dt.timedelta(hours=1)]
    return items

def build_ics(events):
    cal = Calendar()
    now_utc = dt.datetime.now(dt.timezone.utc)
    for ev in events:
        e = Event()
        e.uid = ev["uid"]
        e.name = ev["title"]
        e.begin = ev["start"].astimezone(dt.timezone.utc)
        e.end = ev["end"].astimezone(dt.timezone.utc)
        e.created = now_utc
        e.last_modified = now_utc
        if ev.get("venue"):
            e.location = ev["venue"]
        desc = []
        if ev.get("price"):
            desc.append(ev["price"])
        if ev.get("url"):
            desc.append(ev["url"])
        if desc:
            e.description = " | ".join(desc)
        cal.events.add(e)
    return cal

def load_full_page_html(
    url: str,
    clicks_limit: int = CLICK_LIMIT,
    wait_after_click_ms: int = WAIT_AFTER_CLICK_MS,
    nav_timeout_ms: int = NAV_TIMEOUT_MS,
    headful: bool = True,
) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        try:
            ctx = browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Helsinki",
                user_agent=UA,
                ignore_https_errors=True,
            )
            # Ускоряем: блокируем загрузку изображений
            def _route(route):
                if route.request.resource_type == "image":
                    return route.abort()
                return route.continue_()
            ctx.route("**/*", _route)

            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(nav_timeout_ms)

            page.goto(url, wait_until="load", timeout=nav_timeout_ms)

            # Ждём хотя бы появления блоков карточек (ориентир — дата или слово EUR)
            try:
                page.wait_for_selector("text=/\\d{1,2}:\\d{2}/", timeout=12000)
            except PWTimeout:
                page.wait_for_selector("text=/EUR/", timeout=8000)

            # Закрываем cookie-баннер, если есть
            for patt in (r"(Соглас|Принять|Accept)", r"(Ок|OK)"):
                try:
                    page.get_by_role("button", name=re.compile(patt, re.I)).click(timeout=1500)
                    break
                except Exception:
                    pass

            # Кликаем "Показать больше" много раз
            for i in range(clicks_limit):
                try:
                    btn = page.get_by_role(
                        "button",
                        name=re.compile(r"Показать\s*(больше|ещё|еще)", re.I)
                    )
                    if not btn or not btn.is_visible():
                        break
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click(timeout=4000)
                    try:
                        page.wait_for_load_state("load", timeout=5000)
                    except PWTimeout:
                        pass
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(wait_after_click_ms)
                except Exception:
                    break

            html = page.content()
            return html
        finally:
            browser.close()

# --------------------
# Точка входа
# --------------------
def main():
    # 2 попытки: сначала headful (диагностика), потом headless
    attempts = [
        dict(headful=HEADFUL_FIRST_TRY),
        dict(headful=False),
    ]

    last_err = None
    html = None
    for opts in attempts:
        try:
            print(f"[info] loading page (headful={opts['headful']}) …")
            html = load_full_page_html(URL, headful=opts["headful"])
            break
        except Exception as e:
            last_err = e
            print(f"[warn] load attempt failed: {e!r}")
            time.sleep(1.0)

    if html is None:
        raise last_err or RuntimeError("Не удалось загрузить страницу")

    if DEBUG_HTML:
        with open("page.debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[debug] saved page.debug.html")

    events = extract_events(html)

    if not events:
        print("[warn] событий не найдено. Проверьте page.debug.html и при необходимости пришлите его фрагмент.")
    else:
        print(f"[info] найдено событий: {len(events)}")

    cal = build_ics(events)
    with open(ICS_PATH, "w", encoding="utf-8") as f:
        f.writelines(cal)
    print(f"[ok] Wrote {ICS_PATH} with {len(events)} events")

if __name__ == "__main__":
    main()
