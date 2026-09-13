import concurrent.futures
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('data')
TRADE = DATA / 'trade.json'
KEY = os.getenv('CENSUS_API_KEY', '').strip()

# Query a rotating/high-value set per run so the job stays safely under the platform limit.
# Existing enriched rows are preserved and merged on later runs.
PORT_CODES = [
    '2704',  # Los Angeles
    '2709',  # Long Beach
    '4601',  # New York/Newark
    '1703',  # Savannah
    '5301',  # Houston
    '1601',  # Charleston
]


def fetch_json(url, timeout=8):
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
    for lag in (2, 3):
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
    params = urllib.parse.urlencode({
        'get': fields,
        'time': period,
        'PORT': port,
        'key': KEY,
    })
    url = 'https://api.census.gov/data/timeseries/intltrade/imports/porths?' + params
    return table(url)


def fetch_period(period):
    rows, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(port_rows, period, port): port for port in PORT_CODES}
        for future in concurrent.futures.as_completed(futures):
            port = futures[future]
            try:
                rows.extend(future.result())
            except Exception as exc:
                errors.append(f'{port}: {str(exc)[:100]}')
    return rows, errors


def collect():
    if not KEY or not TRADE.exists():
        return

    trade = json.loads(TRADE.read_text(encoding='utf-8'))
    rows, errors, used_period = [], [], None

    for period in candidate_periods():
        period_rows, period_errors = fetch_period(period)
        errors.extend(period_errors)
        if period_rows:
            rows, used_period = period_rows, period
            break

    if not rows:
        trade.setdefault('errors', [])
        trade['errors'] = (trade['errors'] + ['Census port enrichment returned no records.'] + errors)[:12]
        TRADE.write_text(json.dumps(trade, indent=2, ensure_ascii=False), encoding='utf-8')
        return

    agg = {}
    for r in rows:
        hs = str(r.get('I_COMMODITY') or '')
        # Keep only 2-digit HS totals to control file size and keep the dashboard readable.
        if str(r.get('COMM_LVL') or '').upper() not in ('HS2', '2') and len(hs) != 2:
            continue
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

    new_goods = list(agg.values())
    for g in new_goods:
        if g['quantity'] > 0:
            g['unit_value_usd'] = g['import_value_usd'] / g['quantity']
        else:
            g['quantity'] = None
            g['quantity_unit'] = None

    countries = trade.get('countries') or []
    existing_us = next((c for c in countries if str(c.get('code')) == '842'), None)
    previous_enriched = []
    if existing_us:
        previous_enriched = [g for g in (existing_us.get('goods') or []) if g.get('port') and g.get('partner') not in (None, '', 'World')]

    merged = {}
    for g in previous_enriched + new_goods:
        k = (g.get('hs_code'), g.get('partner'), g.get('port_code'), g.get('port'))
        merged[k] = g
    goods = sorted(merged.values(), key=lambda x: x.get('import_value_usd') or 0, reverse=True)[:500]

    if goods:
        port_totals = {}
        for g in goods:
            k = (g.get('port_code'), g.get('port'))
            port_totals[k] = port_totals.get(k, 0) + (g.get('import_value_usd') or 0)
        ports = [
            {'code': code, 'name': name, 'port': name, 'import_value_usd': value, 'source': 'U.S. Census International Trade — Port HS'}
            for (code, name), value in sorted(port_totals.items(), key=lambda kv: kv[1], reverse=True)
        ]
        replacement = {
            'code': '842', 'name': 'United States', 'period': used_period,
            'goods': goods, 'ports': ports, 'origin_live': True, 'port_live': True,
            'source': 'U.S. Census International Trade — Port HS'
        }
        if existing_us:
            countries[countries.index(existing_us)] = replacement
        else:
            countries.insert(0, replacement)
        trade['countries'] = countries
        trade['source'] = 'UN Comtrade + U.S. Census International Trade'
        trade['port_status'] = f'U.S. origin-country and landing-port detail connected for {len(ports)} ports.'
        trade['status'] = 'Official trade data. U.S. detailed rows contain origin country, HS2 product, import value and landing port from Census Port HS data.'
        trade['updated_at'] = datetime.now(timezone.utc).isoformat()
        trade['errors'] = errors[:8]

    TRADE.write_text(json.dumps(trade, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    collect()
