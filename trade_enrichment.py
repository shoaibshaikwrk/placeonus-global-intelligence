import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('data')
TRADE = DATA / 'trade.json'
KEY = os.getenv('CENSUS_API_KEY', '').strip()

# Verified Schedule D port codes for major U.S. gateways.
PORT_CODES = [
    '2704', # Los Angeles
    '2709', # Long Beach
    '4601', # New York/Newark
    '1703', # Savannah
    '5301', # Houston
    '1601', # Charleston
    '1401', # Norfolk-Newport News
    '2811', # Oakland
    '3001', # Seattle
    '5203', # Port Everglades
    '1303', # Baltimore
    '2904', # Portland, OR
]


def fetch_json(url, timeout=35):
    req = urllib.request.Request(url, headers={'User-Agent': 'PlaceOnUs-Global-Intelligence/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def table(url):
    raw = fetch_json(url)
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    header = raw[0]
    return [dict(zip(header, row)) for row in raw[1:]]


def num(v):
    try:
        return float(v) if v not in (None, '') else None
    except Exception:
        return None


def candidate_periods():
    now = datetime.now(timezone.utc)
    out = []
    for lag in range(2, 8):
        y, m = now.year, now.month - lag
        while m <= 0:
            y -= 1
            m += 12
        out.append(f'{y:04d}-{m:02d}')
    return out


def port_rows(period, port):
    fields = ','.join([
        'PORT_NAME','PORT','CTY_CODE','CTY_NAME','I_COMMODITY','I_COMMODITY_SDESC',
        'COMM_LVL','GEN_VAL_MO','GEN_VAL_YR','VES_WGT_MO','VES_WGT_YR',
        'CNT_VAL_MO','CNT_VAL_YR'
    ])
    base = {
        'get': fields,
        'time': period,
        'PORT': port,
        'COMM_LVL': 'HS2',
        'key': KEY,
    }
    url = 'https://api.census.gov/data/timeseries/intltrade/imports/porths?' + urllib.parse.urlencode(base)
    try:
        return table(url)
    except Exception:
        # Some Census releases reject COMM_LVL as a predicate. Retry and filter HS2 locally.
        base.pop('COMM_LVL', None)
        url = 'https://api.census.gov/data/timeseries/intltrade/imports/porths?' + urllib.parse.urlencode(base)
        return table(url)


def collect():
    if not KEY or not TRADE.exists():
        return

    trade = json.loads(TRADE.read_text(encoding='utf-8'))
    errors = []
    rows = []
    used_period = None

    # Find the newest month that returns actual port records.
    for period in candidate_periods():
        period_rows = []
        for port in PORT_CODES:
            try:
                period_rows.extend(port_rows(period, port))
            except Exception as exc:
                errors.append(f'{port}: {str(exc)[:100]}')
        if period_rows:
            rows = period_rows
            used_period = period
            break

    if not rows:
        trade.setdefault('errors', [])
        trade['errors'] = (trade['errors'] + ['Census port enrichment returned no records.'] + errors)[:12]
        TRADE.write_text(json.dumps(trade, indent=2, ensure_ascii=False), encoding='utf-8')
        return

    # Aggregate exact HS2 + origin-country + landing-port combinations.
    agg = {}
    for r in rows:
        hs = str(r.get('I_COMMODITY') or '')
        if len(hs) != 2 or not hs.isdigit():
            continue
        origin = (r.get('CTY_NAME') or '').strip()
        port_name = (r.get('PORT_NAME') or '').strip()
        port_code = str(r.get('PORT') or '')
        value = num(r.get('GEN_VAL_YR') or r.get('GEN_VAL_MO'))
        if not origin or not port_name or value is None or value <= 0:
            continue
        key = (hs, origin, port_code, port_name)
        rec = agg.setdefault(key, {
            'hs_code': hs,
            'product': r.get('I_COMMODITY_SDESC') or f'HS {hs}',
            'partner': origin,
            'partner_code': str(r.get('CTY_CODE') or ''),
            'import_value_usd': 0.0,
            'quantity': 0.0,
            'quantity_unit': 'kg vessel',
            'unit_value_usd': None,
            'port': port_name,
            'port_code': port_code,
            'containerized_value_usd': 0.0,
            'source': 'U.S. Census International Trade — Port HS'
        })
        rec['import_value_usd'] += value
        weight = num(r.get('VES_WGT_YR') or r.get('VES_WGT_MO')) or 0.0
        rec['quantity'] += weight
        rec['containerized_value_usd'] += num(r.get('CNT_VAL_YR') or r.get('CNT_VAL_MO')) or 0.0

    goods = list(agg.values())
    for g in goods:
        if g['quantity'] > 0:
            g['unit_value_usd'] = g['import_value_usd'] / g['quantity']
        else:
            g['quantity'] = None
            g['quantity_unit'] = None
    goods.sort(key=lambda x: x['import_value_usd'], reverse=True)
    goods = goods[:400]

    port_totals = {}
    for g in goods:
        k = (g['port_code'], g['port'])
        port_totals[k] = port_totals.get(k, 0) + g['import_value_usd']
    ports = [
        {'code': code, 'name': name, 'port': name, 'import_value_usd': value, 'source': 'U.S. Census International Trade — Port HS'}
        for (code, name), value in sorted(port_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    if goods:
        countries = trade.get('countries') or []
        us = next((c for c in countries if str(c.get('code')) == '842'), None)
        replacement = {
            'code': '842',
            'name': 'United States',
            'period': used_period,
            'goods': goods,
            'ports': ports,
            'origin_live': True,
            'port_live': True,
            'source': 'U.S. Census International Trade — Port HS'
        }
        if us:
            countries[countries.index(us)] = replacement
        else:
            countries.insert(0, replacement)
        trade['countries'] = countries
        trade['source'] = 'UN Comtrade + U.S. Census International Trade'
        trade['port_status'] = f'U.S. origin-country and landing-port detail connected for {len(ports)} major ports.'
        trade['status'] = 'Official trade data. U.S. detailed rows contain reported origin country, HS2 product, import value and landing port from Census Port HS data.'
        trade['updated_at'] = datetime.now(timezone.utc).isoformat()
        trade['errors'] = errors[:8]

    TRADE.write_text(json.dumps(trade, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    collect()
