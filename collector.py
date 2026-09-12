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
    text = ' '.join(i.get('headline', '').lower() for i in news.get('items', []))
    def count(words):
        return sum(text.count(w) for w in words)
    geo = min(100, 30 + count(['war', 'attack', 'missile', 'drone', 'sanction']) * 7)
    shipping = min(100, 25 + count(['tanker', 'shipping', 'vessel', 'port', 'strait', 'red sea']) * 6)
    oil = min(100, 25 + count(['oil', 'crude', 'pipeline', 'refinery', 'opec']) * 5)
    freight = min(100, 20 + count(['freight', 'shipping', 'port']) * 5)
    insurance = min(100, round((geo + shipping) / 2))
    return {
        'source': 'PlaceOnUs derived from current headlines',
        'live': True,
        'scores': {'geopolitical': geo, 'oil_supply': oil, 'shipping': shipping, 'freight': freight, 'marine_insurance': insurance},
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
