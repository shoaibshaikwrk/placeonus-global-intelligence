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
    ('842', 'United States'), ('156', 'China'), ('356', 'India'),
    ('682', 'Saudi Arabia'), ('784', 'United Arab Emirates'),
    ('276', 'Germany'), ('826', 'United Kingdom'), ('392', 'Japan'),
    ('410', 'South Korea'), ('124', 'Canada')
]


def first_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value
    return None


def as_number(value):
    try:
        return float(value) if value not in (None, '') else None
    except Exception:
        return None


def comtrade_rows(reporter_code, period, partner_code='0', max_records=100):
    params = {
        'reporterCode': reporter_code,
        'period': str(period),
        'flowCode': 'M',
        'cmdCode': 'AG2',
        'partnerCode': partner_code,
        'partner2Code': '0',
        'customsCode': 'C00',
        'motCode': '0',
        'maxRecords': str(max_records)
    }
    url = 'https://comtradeapi.un.org/public/v1/preview/C/A/HS?' + urllib.parse.urlencode(params)
    raw = fetch_json(url, timeout=40)
    return raw.get('data') or []


def normalize_trade_row(row):
    value_num = as_number(first_value(row, 'primaryValue', 'TradeValue', 'tradeValue', 'fobvalue', 'cifvalue'))
    qty_raw = first_value(row, 'qty', 'Qty', 'quantity', 'netWgt', 'NetWeight')
    qty_num = as_number(qty_raw)
    unit = first_value(row, 'qtyUnitAbbr', 'QtyUnitAbbr', 'qtyUnitCode', 'netWgtUnit')
    if unit in (-1, '-1', 0, '0'):
        unit = None
    unit_value = value_num / qty_num if value_num is not None and qty_num not in (None, 0) else None
    return {
        'hs_code': str(first_value(row, 'cmdCode', 'CmdCode', 'commodityCode') or ''),
        'product': first_value(row, 'cmdDescE', 'cmdDesc', 'CmdDescE', 'commodityDesc') or 'HS commodity',
        'partner': first_value(row, 'partnerDesc', 'PartnerDesc', 'partnerName') or 'World',
        'partner_code': str(first_value(row, 'partnerCode', 'PartnerCode') or ''),
        'import_value_usd': value_num,
        'quantity': qty_num if qty_num not in (0, None) else None,
        'quantity_unit': unit or ('kg' if first_value(row, 'netWgt', 'NetWeight') not in (None, 0, '0') else None),
        'unit_value_usd': unit_value,
        'port': None,
        'port_code': None,
        'source': 'UN Comtrade'
    }


def census_table(url):
    raw = fetch_json(url, timeout=45)
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    headers = raw[0]
    return [dict(zip(headers, row)) for row in raw[1:]]


def census_latest_month():
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month - 2
    if month <= 0:
        year -= 1
        month += 12
    return f'{year:04d}-{month:02d}'


def census_us_origin_rows(period):
    key = os.getenv('CENSUS_API_KEY', '').strip()
    if not key:
        return []
    params = urllib.parse.urlencode({
        'get': 'NAME,I_COMMODITY_LABEL,I_COMMODITY,GEN_VAL_MO,GEN_VAL_YR,YEAR,MONTH',
        'for': 'usitc standard countries and areas:*',
        'time': period,
        'key': key
    })
    url = 'https://api.census.gov/data/timeseries/intltrade/imports/hsimport?' + params
    return census_table(url)


def census_us_port_rows(period):
    key = os.getenv('CENSUS_API_KEY', '').strip()
    if not key:
        return []
    fields = 'PORT_NAME,US_PORT,CTY_CODE,CTY_DESC,I_COMMODITY,I_COMMODITY_SDESC,GEN_VAL_MO,GEN_VAL_YR,VES_WGT_MO,VES_WGT_YR,CNT_VAL_MO,CNT_VAL_YR'
    # The Census geography model nests ports under customs districts. The wildcard-parent
    # request is attempted first; if unsupported by the API, the caller preserves origin data
    # and reports port status instead of fabricating a port.
    params = urllib.parse.urlencode({
        'get': fields,
        'for': 'port:*',
        'in': 'customs district:*',
        'time': period,
        'key': key
    })
    url = 'https://api.census.gov/data/timeseries/intltrade/imports/porthsimport?' + params
    return census_table(url)


def build_us_census_trade():
    period = census_latest_month()
    origins, ports, errors = [], [], []
    try:
        origins = census_us_origin_rows(period)
    except Exception as exc:
        errors.append('origin: ' + str(exc)[:140])
    try:
        ports = census_us_port_rows(period)
    except Exception as exc:
        errors.append('ports: ' + str(exc)[:140])

    origin_goods = []
    for row in origins:
        value = as_number(row.get('GEN_VAL_YR') or row.get('GEN_VAL_MO'))
        hs = str(row.get('I_COMMODITY') or '')
        country = row.get('NAME') or row.get('CTY_DESC')
        if not hs or not country or value is None or value <= 0:
            continue
        origin_goods.append({
            'hs_code': hs,
            'product': row.get('I_COMMODITY_LABEL') or 'HS commodity',
            'partner': country,
            'partner_code': str(row.get('usitc standard countries and areas') or ''),
            'import_value_usd': value,
            'quantity': None,
            'quantity_unit': None,
            'unit_value_usd': None,
            'port': None,
            'port_code': None,
            'source': 'U.S. Census International Trade'
        })

    port_goods, port_totals = [], {}
    for row in ports:
        value = as_number(row.get('GEN_VAL_YR') or row.get('GEN_VAL_MO'))
        hs = str(row.get('I_COMMODITY') or '')
        port_name = row.get('PORT_NAME')
        origin = row.get('CTY_DESC') or 'All trading partners'
        if not hs or not port_name or value is None or value <= 0:
            continue
        weight = as_number(row.get('VES_WGT_YR') or row.get('VES_WGT_MO'))
        port_code = str(row.get('US_PORT') or row.get('port') or '')
        port_goods.append({
            'hs_code': hs,
            'product': row.get('I_COMMODITY_SDESC') or 'HS commodity',
            'partner': origin,
            'partner_code': str(row.get('CTY_CODE') or ''),
            'import_value_usd': value,
            'quantity': weight if weight not in (None, 0) else None,
            'quantity_unit': 'kg' if weight not in (None, 0) else None,
            'unit_value_usd': value / weight if weight not in (None, 0) else None,
            'port': port_name,
            'port_code': port_code,
            'containerized_value_usd': as_number(row.get('CNT_VAL_YR') or row.get('CNT_VAL_MO')),
            'source': 'U.S. Census International Trade'
        })
        port_totals[(port_code, port_name)] = port_totals.get((port_code, port_name), 0) + value

    # Prefer port-specific rows because they include both origin-country and port-of-entry.
    goods = sorted(port_goods if port_goods else origin_goods, key=lambda x: x.get('import_value_usd') or 0, reverse=True)[:250]
    top_ports = [
        {'code': code, 'name': name, 'import_value_usd': value, 'source': 'U.S. Census International Trade'}
        for (code, name), value in sorted(port_totals.items(), key=lambda kv: kv[1], reverse=True)[:15]
    ]
    return {
        'period': period,
        'goods': goods,
        'ports': top_ports,
        'port_live': bool(port_goods),
        'origin_live': bool(origin_goods or port_goods),
        'errors': errors
    }


def trade_data():
    current_year = datetime.now(timezone.utc).year
    candidate_years = [current_year - 1, current_year - 2]
    countries, used_periods, errors = [], set(), []

    census_us = build_us_census_trade()
    if census_us['goods']:
        countries.append({
            'code': '842', 'name': 'United States', 'period': census_us['period'],
            'goods': census_us['goods'], 'ports': census_us['ports'],
            'origin_live': census_us['origin_live'], 'port_live': census_us['port_live'],
            'source': 'U.S. Census International Trade'
        })
        used_periods.add(census_us['period'])
    if census_us['errors']:
        errors.extend(census_us['errors'])

    for reporter_code, reporter_name in TRADE_REPORTERS:
        if reporter_code == '842' and census_us['goods']:
            continue
        rows, used_period = [], None
        for period in candidate_years:
            try:
                rows = comtrade_rows(reporter_code, period)
                if rows:
                    used_period = period
                    break
            except Exception as exc:
                errors.append(f'{reporter_name}: {str(exc)[:100]}')
        if not rows:
            continue
        goods = [normalize_trade_row(r) for r in rows]
        goods = [g for g in goods if g['hs_code'] and g['hs_code'] not in ('TOTAL', 'AG2') and g['import_value_usd'] is not None]
        goods.sort(key=lambda x: x.get('import_value_usd') or 0, reverse=True)
        if goods:
            countries.append({
                'code': reporter_code, 'name': reporter_name, 'period': str(used_period),
                'goods': goods[:40], 'ports': [], 'origin_live': False, 'port_live': False,
                'source': 'UN Comtrade'
            })
            used_periods.add(str(used_period))

    if countries:
        return {
            'source': 'UN Comtrade + U.S. Census International Trade',
            'live': True,
            'status': 'Official trade data. U.S. rows use Census origin/port detail when available; other countries use UN Comtrade aggregate trade data.',
            'period': ', '.join(sorted(used_periods, reverse=True)),
            'countries': countries,
            'port_status': 'U.S. port-of-entry data connected' if census_us['port_live'] else 'U.S. Census origin data connected; port wildcard query did not return port rows in this refresh.',
            'errors': errors[:8],
            'container': {
                'source': 'Freight rate provider not connected', 'live': False,
                'status': 'Container spot rates require a freight-rate provider; Census containerized value is trade value, not a freight quote.',
                'global_40ft_usd': None, 'routes': []
            }
        }

    previous = read_existing('trade.json', {}) or {}
    if previous.get('countries'):
        previous['live'] = False
        previous['stale'] = True
        previous['status'] = 'Trade refresh failed; showing the previous successful snapshot.'
        previous['errors'] = errors[:8]
        return previous
    return {
        'source': 'UN Comtrade + U.S. Census International Trade', 'live': False,
        'status': 'No usable trade records returned in this refresh.', 'period': None,
        'countries': [], 'errors': errors[:8],
        'container': {'source': 'Freight rate provider not connected', 'live': False, 'status': 'Container spot rates require a freight-rate provider.', 'global_40ft_usd': None, 'routes': []}
    }


AISSTREAM_BOXES = [
    [[22.0, 54.0], [28.5, 60.5]], [[11.0, 41.0], [30.5, 45.5]],
    [[29.0, 31.0], [31.5, 33.5]], [[40.0, 27.0], [47.5, 42.5]],
    [[0.0, 98.0], [6.5, 106.0]], [[7.0, -83.0], [10.5, -77.0]],
    [[50.5, 2.5], [53.0, 6.0]], [[25.3, -80.7], [26.1, -79.6]]
]


async def collect_aisstream(duration_seconds=60, max_vessels=400):
    key = os.getenv('AISSTREAM_API_KEY', '').strip()
    if not key:
        return {'source': 'AISStream.io', 'live': False, 'status': 'AISSTREAM_API_KEY not configured', 'subscription_confirmed': False, 'messages_received': 0, 'items': []}
    subscription = {'APIKey': key, 'BoundingBoxes': AISSTREAM_BOXES, 'FilterMessageTypes': ['PositionReport', 'StandardClassBPositionReport', 'ExtendedClassBPositionReport']}
    vessels, started, confirmation, total_messages, last_message_type = {}, time.monotonic(), False, 0, None
    try:
        async with websockets.connect('wss://stream.aisstream.io/v0/stream', open_timeout=10, close_timeout=5, ping_interval=20, ping_timeout=20, compression='deflate') as ws:
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
                lat = meta.get('Latitude') if meta.get('Latitude') is not None else body.get('Latitude')
                lon = meta.get('Longitude') if meta.get('Longitude') is not None else body.get('Longitude')
                if not mmsi or lat is None or lon is None:
                    continue
                vessels[str(mmsi)] = {
                    'name': (meta.get('ShipName') or '').strip() or f'MMSI {mmsi}', 'mmsi': str(mmsi), 'imo': None,
                    'vessel_type': msg_type, 'flag': None, 'lat': lat, 'lon': lon, 'speed_knots': body.get('Sog'),
                    'course': body.get('Cog'), 'heading': body.get('TrueHeading'), 'destination': None, 'eta': None,
                    'last_port': None, 'timestamp': datetime.now(timezone.utc).isoformat(), 'live': True
                }
    except Exception as exc:
        return {'source': 'AISStream.io', 'live': False, 'status': 'AIS stream error: ' + str(exc)[:180], 'subscription_confirmed': confirmation, 'messages_received': total_messages, 'last_message_type': last_message_type, 'items': list(vessels.values())}
    status = f'live - {len(vessels)} vessels sampled in {duration_seconds}s' if vessels else (f'connected and subscription confirmed, but no vessel positions arrived in {duration_seconds}s' if confirmation else f'WebSocket opened but subscription was not confirmed; {total_messages} messages received')
    return {'source': 'AISStream.io', 'live': bool(vessels), 'status': status, 'subscription_confirmed': confirmation, 'messages_received': total_messages, 'last_message_type': last_message_type, 'items': list(vessels.values())}


def vessel_data():
    return asyncio.run(collect_aisstream())


def derive_risk(news):
    items = news.get('items', [])
    if not items:
        return {'source': 'PlaceOnUs derived from current headlines', 'live': False, 'scores': {}, 'status': 'Risk score unavailable because there are no current news records.', 'method': 'Transparent keyword-pressure heuristic; AI model not yet enabled.'}
    text = ' '.join(i.get('headline', '').lower() for i in items)
    def count(words):
        return sum(text.count(w) for w in words)
    geo = min(100, 30 + count(['war', 'attack', 'missile', 'drone', 'sanction']) * 7)
    shipping = min(100, 25 + count(['tanker', 'shipping', 'vessel', 'port', 'strait', 'red sea']) * 6)
    oil = min(100, 25 + count(['oil', 'crude', 'pipeline', 'refinery', 'opec']) * 5)
    freight = min(100, 20 + count(['freight', 'shipping', 'port']) * 5)
    insurance = min(100, round((geo + shipping) / 2))
    return {'source': 'PlaceOnUs derived from current headlines', 'live': bool(news.get('live')), 'scores': {'geopolitical': geo, 'oil_supply': oil, 'shipping': shipping, 'freight': freight, 'marine_insurance': insurance}, 'status': 'Current' if news.get('live') else 'Calculated from the latest stored news snapshot.', 'method': 'Transparent keyword-pressure heuristic; AI model not yet enabled.'}


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
            news['live'], news['stale'], news['error'] = False, True, str(exc)
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
