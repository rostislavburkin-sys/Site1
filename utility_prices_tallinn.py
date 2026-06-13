"""
Цены на коммунальные услуги в Таллине
─────────────────────────────────────
Электричество : dashboard.elering.ee  (реальное время)
Топливо       : 1182.ee               (ежедневно)
Вода          : фиксированные тарифы  Tallinna Vesi

Использование:
  python utility_prices_tallinn.py          → генерирует index.html
  python utility_prices_tallinn.py --print  → вывод в консоль
"""

import sys
import os
import json
import re
import urllib.request
import datetime

try:
    import requests
    from bs4 import BeautifulSoup
    USE_REQUESTS = True
except ImportError:
    USE_REQUESTS = False

OUTPUT_FILE = "index.html"

FUEL_URL = "https://www.1182.ee/fuelprices"
ELERING_URL = "https://dashboard.elering.ee/api/nps/price/EE/current"
TEADMISEKS_URL = "https://teadmiseks.ee/ru/poleznoe/ceny-na-toplivo/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

FUEL_LABELS = {
    "95":     {"icon": "⛽", "color": "#f5c842"},
    "98":     {"icon": "🔥", "color": "#ff8c42"},
    "Diesel": {"icon": "🛢️",  "color": "#6ec6f5"},
}


# ══════════════════════════════════════════════════════════
#  ЭЛЕКТРИЧЕСТВО
# ══════════════════════════════════════════════════════════

def fetch_electricity():
    req = urllib.request.Request(
        ELERING_URL,
        headers={"Accept": "application/json", "User-Agent": HEADERS["User-Agent"]}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())

    entry = data["data"][0]
    price_mwh = entry["price"]
    price_kwh = price_mwh / 1000
    ts = entry.get("timestamp", 0)
    updated = datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

    return {
        "price_mwh": price_mwh,
        "price_kwh": price_kwh,
        "updated": updated,
    }


# ══════════════════════════════════════════════════════════
#  ТОПЛИВО
# ══════════════════════════════════════════════════════════

def fetch_fuel_1182():
    """Парсит 1182.ee через requests + BeautifulSoup (точнее)."""
    resp = requests.get(FUEL_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    date_str = ""
    for tag in soup.find_all(["h2", "p", "div", "span"]):
        text = tag.get_text(strip=True)
        if "Fuel prices as of" in text:
            date_str = text.replace("Fuel prices as of", "").strip()
            break

    table = soup.find("table")
    if not table:
        raise ValueError("Таблица топлива не найдена.")

    rows = table.find_all("tr")
    header_cells = rows[0].find_all(["th", "td"])

    stations = []
    for cell in header_cells[1:]:
        lines = cell.get_text(separator="\n", strip=True).split("\n")
        stations.append(lines[0].strip())

    result = {"date": date_str, "stations": {s: {} for s in stations}}
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        fuel_type = cells[0].get_text(strip=True)
        if not fuel_type:
            continue
        for i, cell in enumerate(cells[1:]):
            if i >= len(stations):
                break
            try:
                result["stations"][stations[i]][fuel_type] = float(cell.get_text(strip=True))
            except ValueError:
                result["stations"][stations[i]][fuel_type] = None

    return result


def fetch_fuel_teadmiseks():
    """Запасной парсер teadmiseks.ee через urllib (без зависимостей)."""
    req = urllib.request.Request(TEADMISEKS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")

    date_match = re.search(r'посл\.\s*обнов\.\s*([\d.]+)', html)
    date_str = date_match.group(1) if date_match else "?"

    matches = re.findall(
        r'<td[^>]*>\s*(?:<[^>]+>)?\s*(Бензин\s*\d+|Дизель)\s*(?:</[^>]+>)?\s*</td>\s*'
        r'<td[^>]*>\s*([\d.,]+)\s*[€]?\s*</td>',
        html, re.IGNORECASE
    )
    translate = {"Бензин 95": "95", "Бензин 98": "98", "Дизель": "Diesel"}
    prices = {}
    for name, price in matches:
        key = translate.get(name.strip(), name.strip())
        prices[key] = float(price.replace(",", "."))

    # Представляем как один «виртуальный» источник
    return {
        "date": date_str,
        "stations": {"teadmiseks.ee": prices},
    }


def fetch_fuel():
    if USE_REQUESTS:
        try:
            return fetch_fuel_1182(), "1182.ee"
        except Exception as e:
            print(f"  [fuel] 1182.ee недоступен ({e}), пробую teadmiseks.ee...")
    return fetch_fuel_teadmiseks(), "teadmiseks.ee"


def find_cheapest(fuel_data):
    cheapest = {}
    for station, fuels in fuel_data["stations"].items():
        for ftype, price in fuels.items():
            if price is None:
                continue
            if ftype not in cheapest or price < cheapest[ftype]["price"]:
                cheapest[ftype] = {"price": price, "station": station}
    return cheapest


# ══════════════════════════════════════════════════════════
#  ВОДА  (Tallinna Vesi, фиксированный тариф)
# ══════════════════════════════════════════════════════════

def get_water():
    cold  = 1.32
    waste = 0.77
    vat   = 0.22
    total = (cold + waste) * (1 + vat)
    return {"cold": cold, "waste": waste, "vat": vat, "total": total}


# ══════════════════════════════════════════════════════════
#  HTML
# ══════════════════════════════════════════════════════════

def build_html(elec, fuel_data, fuel_source, water):
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    stations = list(fuel_data["stations"].keys())
    fuel_types = list(FUEL_LABELS.keys())
    cheapest = find_cheapest(fuel_data)

    # ── карточки топлива ──
    fuel_cards = ""
    for ft in fuel_types:
        if ft not in cheapest:
            continue
        meta = FUEL_LABELS[ft]
        info = cheapest[ft]
        fuel_cards += f"""
          <div class="best-card">
            <div class="best-icon">{meta['icon']}</div>
            <div class="best-fuel" style="color:{meta['color']}">{ft}</div>
            <div class="best-price">{info['price']:.3f}<span class="unit"> €/л</span></div>
            <div class="best-station">{info['station']}</div>
          </div>"""

    # ── строки таблицы топлива ──
    th_html = "<th>Топливо</th>" + "".join(f"<th>{s}</th>" for s in stations)
    table_rows = ""
    for ft in fuel_types:
        meta = FUEL_LABELS[ft]
        prices = [fuel_data["stations"][s].get(ft) for s in stations]
        valid = [p for p in prices if p is not None]
        min_p = min(valid) if valid else None

        cells = f"""<td class="fuel-label">
              <span class="fuel-dot" style="background:{meta['color']}"></span>{ft}
            </td>"""
        for price in prices:
            if price is None:
                cells += '<td class="price-cell">—</td>'
            else:
                best = price == min_p
                badge = '<span class="badge">лучшая</span>' if best else ""
                cls = "price-cell best-price-cell" if best else "price-cell"
                cells += f'<td class="{cls}"><span class="price-val">{price:.3f}</span> €{badge}</td>'
        table_rows += f"<tr>{cells}</tr>\n"

    # ── вода ──
    water_rows = f"""
      <div class="water-row">
        <span class="wlabel">Холодная вода</span>
        <span class="wvalue">{water['cold']:.2f} <span class="unit">€/м³</span></span>
      </div>
      <div class="water-row">
        <span class="wlabel">Канализация</span>
        <span class="wvalue">{water['waste']:.2f} <span class="unit">€/м³</span></span>
      </div>
      <div class="water-divider"></div>
      <div class="water-row total-row">
        <span class="wlabel">Итого с НДС {int(water['vat']*100)}%</span>
        <span class="wvalue accent">{water['total']:.2f} <span class="unit">€/м³</span></span>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Тарифы — Таллин</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #0f1117;
      --surface: #181c27;
      --border:  #252a38;
      --text:    #e4e8f0;
      --muted:   #6b7280;
      --accent:  #f5c842;
      --blue:    #6ec6f5;
      --orange:  #ff8c42;
    }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      min-height: 100vh;
      padding: 2.5rem 1rem 5rem;
    }}

    /* ── шапка ── */
    .page-header {{ max-width: 1000px; margin: 0 auto 3rem; }}
    .eyebrow {{
      font-size: .7rem; font-weight: 600; letter-spacing: .14em;
      text-transform: uppercase; color: var(--muted); margin-bottom: .45rem;
    }}
    .page-title {{ font-size: 2.1rem; font-weight: 700; line-height: 1.15; }}
    .page-title span {{ color: var(--accent); }}
    .page-meta {{ margin-top: .55rem; font-size: .8rem; color: var(--muted); }}

    /* ── сетка секций ── */
    .sections {{ max-width: 1000px; margin: 0 auto; display: flex; flex-direction: column; gap: 3rem; }}

    /* ── заголовок секции ── */
    .section-head {{
      display: flex; align-items: baseline; gap: .75rem;
      margin-bottom: 1.25rem;
      padding-bottom: .75rem;
      border-bottom: 1px solid var(--border);
    }}
    .section-icon {{ font-size: 1.1rem; }}
    .section-title {{
      font-size: .75rem; font-weight: 700; letter-spacing: .1em;
      text-transform: uppercase; color: var(--muted);
    }}
    .section-src {{ margin-left: auto; font-size: .7rem; color: var(--border); }}
    .section-src a {{ color: var(--muted); text-decoration: underline dotted; }}

    /* ══ ЭЛЕКТРИЧЕСТВО ══ */
    .elec-body {{ display: flex; gap: 1.5rem; flex-wrap: wrap; }}
    .elec-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.4rem 1.8rem;
      flex: 1; min-width: 170px;
    }}
    .elec-label {{ font-size: .7rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-bottom: .35rem; }}
    .elec-value {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 2.2rem; font-weight: 500; line-height: 1;
    }}
    .elec-value.big {{ color: var(--accent); }}
    .elec-updated {{ margin-top: .9rem; font-size: .72rem; color: var(--muted); }}

    /* ══ ТОПЛИВО ══ */
    .best-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .best-card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; padding: 1.2rem 1.4rem;
      display: flex; flex-direction: column; gap: .2rem;
    }}
    .best-icon {{ font-size: 1.3rem; margin-bottom: .15rem; }}
    .best-fuel {{ font-size: .7rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .best-price {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 1.85rem; font-weight: 500; color: var(--text); line-height: 1;
    }}
    .best-station {{ font-size: .73rem; color: var(--muted); margin-top: .1rem; }}

    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .88rem; }}
    thead th {{
      text-align: left; padding: .55rem 1rem;
      font-size: .68rem; font-weight: 600; letter-spacing: .08em;
      text-transform: uppercase; color: var(--muted);
      border-bottom: 1px solid var(--border);
    }}
    tbody tr {{ border-bottom: 1px solid var(--border); transition: background .12s; }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: rgba(255,255,255,.025); }}
    td {{ padding: .8rem 1rem; vertical-align: middle; }}
    .fuel-label {{
      font-weight: 600; white-space: nowrap;
      display: flex; align-items: center; gap: .5rem;
    }}
    .fuel-dot {{ width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }}
    .price-cell {{
      font-family: 'JetBrains Mono', monospace;
      font-size: .85rem; color: var(--muted); white-space: nowrap;
    }}
    .price-val {{ color: var(--text); }}
    .best-price-cell .price-val {{ color: var(--accent); font-weight: 700; }}
    .badge {{
      display: inline-block; font-family: 'Inter', sans-serif;
      font-size: .58rem; font-weight: 700; letter-spacing: .06em;
      text-transform: uppercase;
      background: rgba(245,200,66,.14); color: var(--accent);
      border-radius: 4px; padding: 1px 5px; margin-left: .35rem;
      vertical-align: middle;
    }}

    /* ══ ВОДА ══ */
    .water-body {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; padding: 1.25rem 1.5rem;
      max-width: 380px;
    }}
    .water-row {{
      display: flex; justify-content: space-between; align-items: baseline;
      padding: .45rem 0;
    }}
    .wlabel {{ font-size: .85rem; color: var(--muted); }}
    .wvalue {{
      font-family: 'JetBrains Mono', monospace;
      font-size: .95rem; color: var(--text);
    }}
    .wvalue.accent {{ color: var(--blue); font-weight: 700; font-size: 1.1rem; }}
    .water-divider {{ border-top: 1px solid var(--border); margin: .35rem 0; }}
    .total-row .wlabel {{ color: var(--text); font-weight: 600; }}

    /* общие */
    .unit {{ font-size: .72em; color: var(--muted); }}
    .footer {{
      max-width: 1000px; margin: 3rem auto 0;
      font-size: .72rem; color: var(--muted);
      display: flex; justify-content: space-between; flex-wrap: wrap; gap: .4rem;
    }}

    @media (max-width: 600px) {{
      .elec-body {{ flex-direction: column; }}
      .best-grid {{ grid-template-columns: repeat(3, 1fr); gap: .6rem; }}
      .best-price {{ font-size: 1.35rem; }}
    }}
  </style>
</head>
<body>

  <div class="page-header">
    <div class="eyebrow">Таллин · Эстония</div>
    <h1 class="page-title">Актуальные <span>тарифы</span></h1>
    <p class="page-meta">Электричество, топливо и вода — данные на сегодня</p>
  </div>

  <div class="sections">

    <!-- ── ЭЛЕКТРИЧЕСТВО ── -->
    <section>
      <div class="section-head">
        <span class="section-icon">⚡</span>
        <span class="section-title">Электричество</span>
        <span class="section-src"><a href="https://dashboard.elering.ee" target="_blank">elering.ee</a></span>
      </div>
      <div class="elec-body">
        <div class="elec-card">
          <div class="elec-label">Биржевая цена NPS</div>
          <div class="elec-value big">{elec['price_mwh']:.2f}<span class="unit"> €/МВт·ч</span></div>
          <div class="elec-updated">Обновлено: {elec['updated']}</div>
        </div>
        <div class="elec-card">
          <div class="elec-label">За киловатт-час</div>
          <div class="elec-value">{elec['price_kwh']:.4f}<span class="unit"> €/кВт·ч</span></div>
          <div class="elec-updated">Цена без НДС и сетевых сборов</div>
        </div>
      </div>
    </section>

    <!-- ── ТОПЛИВО ── -->
    <section>
      <div class="section-head">
        <span class="section-icon">⛽</span>
        <span class="section-title">Топливо · Таллин</span>
        <span class="section-src">
          Данные от {fuel_data['date']} ·
          <a href="{FUEL_URL if fuel_source == '1182.ee' else TEADMISEKS_URL}" target="_blank">{fuel_source}</a>
        </span>
      </div>
      <div class="best-grid">{fuel_cards}</div>
      <div class="table-wrap">
        <table>
          <thead><tr>{th_html}</tr></thead>
          <tbody>{table_rows}</tbody>
        </table>
      </div>
    </section>

    <!-- ── ВОДА ── -->
    <section>
      <div class="section-head">
        <span class="section-icon">💧</span>
        <span class="section-title">Вода</span>
        <span class="section-src"><a href="https://www.tallinnavesi.ee" target="_blank">tallinnavesi.ee</a></span>
      </div>
      <div class="water-body">{water_rows}</div>
    </section>

  </div>

  <div class="footer">
    <span>Сформировано: {now}</span>
    <span>Тарифы Tallinna Vesi актуальны на 2025 г.</span>
  </div>

</body>
</html>"""


# ══════════════════════════════════════════════════════════
#  КОНСОЛЬ
# ══════════════════════════════════════════════════════════

def print_report(elec, fuel_data, water):
    cheapest = find_cheapest(fuel_data)
    fuel_types = list(FUEL_LABELS.keys())
    stations = list(fuel_data["stations"].keys())
    print("\n⚡  ЭЛЕКТРИЧЕСТВО")
    print(f"   {elec['price_mwh']:.2f} €/МВт·ч  ({elec['price_kwh']:.4f} €/кВт·ч)")
    print(f"   Обновлено: {elec['updated']}")
    print("\n⛽  ТОПЛИВО  (лучшие цены)")
    for ft in fuel_types:
        if ft in cheapest:
            info = cheapest[ft]
            print(f"   {ft:<8} {info['price']:.3f} € — {info['station']}")
    print("\n💧  ВОДА (Tallinna Vesi)")
    print(f"   Холодная: {water['cold']:.2f} €/м³   Канализация: {water['waste']:.2f} €/м³")
    print(f"   Итого с НДС: {water['total']:.2f} €/м³")
    print()


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print_mode = "--print" in sys.argv

    print("Загружаю данные...")

    # электричество
    try:
        print("  ⚡ elering.ee...")
        elec = fetch_electricity()
    except Exception as e:
        print(f"  [elec] ошибка: {e}")
        elec = {"price_mwh": 0, "price_kwh": 0, "updated": "недоступно"}

    # топливо
    try:
        print("  ⛽ топливо...")
        fuel_data, fuel_source = fetch_fuel()
    except Exception as e:
        print(f"  [fuel] ошибка: {e}")
        fuel_data = {"date": "?", "stations": {}}
        fuel_source = "?"

    # вода
    water = get_water()

    if print_mode:
        print_report(elec, fuel_data, water)
    else:
        html = build_html(elec, fuel_data, fuel_source, water)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ Готово → {os.path.abspath(OUTPUT_FILE)}")
