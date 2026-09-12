import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import websockets

DATA = Path('data')
DATA.mkdir(exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()


def write(name, payload):
    payload['updated_at'] = NOW
    (DATA / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def read_existing(name, fallback=None):
    path = DATA / name
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'PlaceOnUs-Global-Intelligence/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def gdelt_news():
    query = '(oil OR crude OR tanker OR shipping OR freight OR sanctions OR war OR pipeline OR refinery OR port OR OPEC OR LNG)'
    params = urllib.parse.urlencode({'query': query, 'mode': 'artlist', 'maxrecords': 40, 'timespan': '1h', 'sort': 'datedesc', 'format': 'json'})
    raw = fetch_json('https://api.gdeltproject.org/api/v2/doc/doc?' + params)
    items, seen = [], set()
    for a in raw.get('articles', []):
        url = a.get('url')
        title = (a.get('title') or '').strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        items.append({
            'headline': title,
            'source': a.get('domain') or 'GDELT source',
            'region': a.get('sourcecountry') or 'Global',
            'language': a.get('language'),
            'published_at': a.get('seendate'),
            'url': url,
            'image': a.get('socialimage'),
            'category': 'GLOBAL INTELLIGENCE',
            'summary': 'Current article discovered through GDELT. Open the source for full reporting.',
            'live': True
        })
    return {'source': 'GDELT DOC 2.0', 'live': True, 'items': items[:30]}


def eia_series(series_id):
    key = os.getenv('EIA_API_KEY', '').strip()
    if not key:
        return None
    url = f'https://api.eia.gov/v2/seriesid/{urllib.parse.quote(series_id)}?api_key={urllib.parse.quote(key)}&length=8'
    return fetch_json(url)


def latest_eia_value(raw):
    try:
        rows = raw['response']['data']
        if not rows:
            return None
        row = rows[0]
        value = row.get('value')
        return {'price': float(value) if value is not None else None, 'period': row.get('period')}
    except Exception:
        return None


def market_data():
    instruments, source = [], []
    try:
        wti = latest_eia_value(eia_series('PET.RWTC.D'))
        brent = latest_eia_value(eia_series('PET.RBRTE.D'))
        if wti:
            instruments.append({'name': 'WTI', 'price': wti['price'], 'trend': 'official EIA spot', 'signal_score': None, 'period': wti['period'], 'live': True})
        if brent:
            instruments.append({'name': 'Brent', 'price': brent['price'], 'trend': 'official EIA spot', 'signal_score': None, 'period': brent['period'], 'live': True})
        if wti or brent:
            source.append('EIA')
    except Exception as exc:
        source.append('EIA error: ' + str(exc)[:120])
    for name in ['Gold', 'S&P 500', 'Bitcoin']:
        instruments.append({'name': name, 'price': None, 'trend': 'data source not connected', 'signal_score': None, 'live': False})
    return {'source': source or ['No market provider connected'], 'instruments': instruments, 'live': any(x.get('live') for x in instruments)}


TRADE_REPORTERS = [
    ('842', 'United States'),
    ('156', 'China'),
    ('356', 'India'),
    ('682', 'Saudi Arabia'),
    ('784', 'United Arab Emirates'),
    ('276', 'Germany'),
    ('826', 'United Kingdom'),
    ('392', 'Japan'),
    ('410', 'South Korea'),
    ('124', 'Canada'),
]


def comtrade_rows(reporter_code, period):
    params = urllib.parse.urlencode({
        'reporterCode': reporter_code,
        'period': str(period),
        'flowCode': 'M',
        'cmdCode': 'AG2',
        'partnerCode': '0',
        'partner2Code': '0',
        'customsCode': 'C00',
        'motCode': '0',
        'maxRecords': '100'
    })
    url = 'https://comtradeapi.un.org/public/v1/preview/C/A/HS?' + params
    raw = fetch_json(url, timeout=40)
    return raw.get('data') or []


def first_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value
    return None


def normalize_trade_row(row):
    value = first_value(row, 'primaryValue', 'TradeValue', 'tradeValue', 'fobvalue', 'cifvalue')
    qty = first_value(row, 'qty', 'Qty', 'quantity', 'netWgt', 'NetWeight')
    try:
        value_num = float(value) if value is not None else None
    except Exception:
        value_num = None
    try:
        qty_num = float(qty) if qty is not None else None
    except Exception:
        qty_num = None
    unit_value = None
    if value_num is not None and qty_num not in (None, 0):
        unit_value = value_num / qty_num
    return {
        'hs_code': str(first_value(row, 'cmdCode', 'CmdCode', 'commodityCode') or ''),
        'product': first_value(row, 'cmdDescE', 'cmdDesc', 'CmdDescE', 'commodityDesc') or 'Unspecified commodity',
        'partner': first_value(row, 'partnerDesc', 'PartnerDesc', 'partnerName') or 'World',
        'import_value_usd': value_num,
        'quantity': qty_num,
        'quantity_unit': first_value(row, 'qtyUnitAbbr', 'QtyUnitAbbr', 'qtyUnitCode', 'netWgtUnit') or ('kg' if first_value(row, 'netWgt', 'NetWeight') is not None else None),
        'unit_value_usd': unit_value,
    }


def trade_data():
    current_year = datetime.now(timezone.utc).year
    candidate_years = [current_year - 1, current_year - 2]
    countries = []
    used_periods = set()
    errors = []

    for reporter_code, reporter_name in TRADE_REPORTERS:
        rows = []
        used_period = None
        for period in candidate_years:
            try:
                rows = comtrade_rows(reporter_code, period)
                if rows:
                    used_period = period
                    break
            except Exception as exc:
                errors.append(f'{reporter_name}: {str(exc)[:80]}')
        if not rows:
            continue

        goods = []
        for row in rows:
            item = normalize_trade_row(row)
            if item['hs_code'] and item['hs_code'] not in ('TOTAL', 'AG2') and item['import_value_usd'] is not None:
                goods.append(item)
        goods.sort(key=lambda x: x.get('import_value_usd') or 0, reverse=True)
        goods = goods[:30]
        if goods:
            countries.append({'code': reporter_code, 'name': reporter_name, 'period': str(used_period), 'goods': goods})
            used_periods.add(str(used_period))

    if countries:
        period_label = ', '.join(sorted(used_periods, reverse=True))
        return {
            'source': 'UN Comtrade',
            'live': True,
            'status': f'Official annual merchandise import data for {len(countries)} countries.',
            'period': period_label,
            'countries': countries,
            'container': {
                'source': 'Freight rate provider not connected',
                'live': False,
                'status': 'Container spot rates are separate from UN Comtrade and require a freight-rate provider.',
                'global_40ft_usd': None,
                'routes': []
            }
        }

    previous = read_existing('trade.json', {}) or {}
    if previous.get('countries'):
        previous['live'] = False
        previous['stale'] = True
        previous['status'] = 'UN Comtrade refresh failed; showing the previous successful snapshot.'
        if errors:
            previous['error'] = '; '.join(errors[:5])
        return previous

    return {
        'source': 'UN Comtrade',
        'live': False,
        'status': 'UN Comtrade returned no usable import records in this refresh.',
        'period': None,
        'countries': [],
        'error': '; '.join(errors[:5]) if errors else None,
        'container': {
            'source': 'Freight rate provider not connected',
            'live': False,
            'status': 'Container spot rates are separate from UN Comtrade and require a freight-rate provider.',
            'global_40ft_usd': None,
            'routes': []
        }
    }


AISSTREAM_BOXES = [
    [[22.0, 54.0], [28.5, 60.5]],
    [[11.0, 41.0], [30.5, 45.5]],
    [[29.0, 31.0], [31.5, 33.5]],
    [[40.0, 27.0], [47.5, 42.5]],
    [[0.0, 98.0], [6.5, 106.0]],
    [[7.0, -83.0], [10.5, -77.0]],
    [[50.5, 2.5], [53.0, 6.0]],
    [[25.3, -80.7], [26.1, -79.6]],
]


async def collect_aisstream(duration_seconds=60, max_vessels=400):
    key = os.getenv('AISSTREAM_API_KEY', '').strip()
    if not key:
        return {'source': 'AISStream.io', 'live': False, 'status': 'AISSTREAM_API_KEY not configured', 'subscription_confirmed': False, 'messages_received': 0, 'items': []}

    subscription = {
        'APIKey': key,
        'BoundingBoxes': AISSTREAM_BOXES,
        'FilterMessageTypes': ['PositionReport', 'StandardClassBPositionReport', 'ExtendedClassBPositionReport']
    }
    vessels = {}
    started = time.monotonic()
    confirmation = False
    total_messages = 0
    last_message_type = None

    try:
        async with websockets.connect(
            'wss://stream.aisstream.io/v0/stream',
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            compression='deflate'
        ) as ws:
            await ws.send(json.dumps(subscription))
            while time.monotonic() - started < duration_seconds and len(vessels) < max_vessels:
                remaining = duration_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(5, remaining))
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                event = json.loads(raw)
                total_messages += 1
                msg_type = event.get('MessageType')
                last_message_type = msg_type
                if msg_type == 'SubscriptionConfirmation':
                    confirmation = True
                    continue

                meta = event.get('MetaData') or {}
                body = (event.get('Message') or {}).get(msg_type, {}) or {}

                mmsi = meta.get('MMSI') or body.get('UserID') or body.get('UserId')
                lat = meta.get('Latitude')
                if lat is None:
                    lat = body.get('Latitude')
                lon = meta.get('Longitude')
                if lon is None:
                    lon = body.get('Longitude')

                if not mmsi or lat is None or lon is None:
                    continue

                vessels[str(mmsi)] = {
                    'name': (meta.get('ShipName') or '').strip() or f'MMSI {mmsi}',
                    'mmsi': str(mmsi),
                    'imo': None,
                    'vessel_type': msg_type,
                    'flag': None,
                    'lat': lat,
                    'lon': lon,
                    'speed_knots': body.get('Sog'),
                    'course': body.get('Cog'),
                    'heading': body.get('TrueHeading'),
                    'destination': None,
                    'eta': None,
                    'last_port': None,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'live': True
                }
    except Exception as exc:
        return {
            'source': 'AISStream.io',
            'live': False,
            'status': 'AIS stream error: ' + str(exc)[:180],
            'subscription_confirmed': confirmation,
            'messages_received': total_messages,
            'last_message_type': last_message_type,
            'items': list(vessels.values())
        }

    if vessels:
        status = f'live - {len(vessels)} vessels sampled in {duration_seconds}s'
    elif confirmation:
        status = f'connected and subscription confirmed, but no vessel positions arrived in {duration_seconds}s'
    else:
        status = f'WebSocket opened but subscription was not confirmed; {total_messages} messages received'

    return {
        'source': 'AISStream.io',
        'live': bool(vessels),
        'status': status,
        'subscription_confirmed': confirmation,
        'messages_received': total_messages,
        'last_message_type': last_message_type,
        'items': list(vessels.values())
    }


def vessel_data():
    return asyncio.run(collect_aisstream())


def derive_risk(news):
    items = news.get('items', [])
    if not items:
        return {
            'source': 'PlaceOnUs derived from current headlines',
            'live': False,
            'scores': {},
            'status': 'Risk score unavailable because there are no current news records.',
            'method': 'Transparent keyword-pressure heuristic; AI model not yet enabled.'
        }
    text = ' '.join(i.get('headline', '').lower() for i in items)
    def count(words):
        return sum(text.count(w) for w in words)
    geo = min(100, 30 + count(['war', 'attack', 'missile', 'drone', 'sanction']) * 7)
    shipping = min(100, 25 + count(['tanker', 'shipping', 'vessel', 'port', 'strait', 'red sea']) * 6)
    oil = min(100, 25 + count(['oil', 'crude', 'pipeline', 'refinery', 'opec']) * 5)
    freight = min(100, 20 + count(['freight', 'shipping', 'port']) * 5)
    insurance = min(100, round((geo + shipping) / 2))
    return {
        'source': 'PlaceOnUs derived from current headlines',
        'live': bool(news.get('live')),
        'scores': {'geopolitical': geo, 'oil_supply': oil, 'shipping': shipping, 'freight': freight, 'marine_insurance': insurance},
        'status': 'Current' if news.get('live') else 'Calculated from the latest stored news snapshot.',
        'method': 'Transparent keyword-pressure heuristic; AI model not yet enabled.'
    }


def main():
    status = {'sources': {}}

    try:
        news = gdelt_news()
        write('news.json', news)
        status['sources']['news'] = 'ok'
    except Exception as exc:
        previous = read_existing('news.json', {'source': 'GDELT', 'live': False, 'items': []}) or {'source': 'GDELT', 'live': False, 'items': []}
        if previous.get('items'):
            news = previous
            news['live'] = False
            news['stale'] = True
            news['error'] = str(exc)
            write('news.json', news)
            status['sources']['news'] = 'stale'
        else:
            news = {'source': 'GDELT', 'live': False, 'items': [], 'error': str(exc)}
            write('news.json', news)
            status['sources']['news'] = 'error'

    markets = market_data()
    write('markets.json', markets)
    status['sources']['markets'] = 'ok' if markets.get('live') else 'partial'

    trade = trade_data()
    write('trade.json', trade)
    status['sources']['trade'] = 'ok' if trade.get('live') else 'stale' if trade.get('countries') else 'error'

    vessels = vessel_data()
    write('vessels.json', vessels)
    status['sources']['vessels'] = 'ok' if vessels.get('live') else 'connected_no_positions' if vessels.get('subscription_confirmed') else 'error'

    write('risk.json', derive_risk(news))
    write('freight.json', {'source': 'Not connected', 'live': False, 'lanes': [], 'status': 'Licensed freight source required'})
    write('trends.json', {'source': 'PlaceOnUs', 'live': False, 'assets': [], 'status': 'Will activate after historical market feed is connected'})
    status['refresh_minutes'] = 10
    status['site'] = 'news.placeonus.com'
    write('system-status.json', status)


if __name__ == '__main__':
    main()
