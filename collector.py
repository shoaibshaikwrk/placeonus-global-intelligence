import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('data')
DATA.mkdir(exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()

def write(name, payload):
    payload['updated_at'] = NOW
    (DATA / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent':'PlaceOnUs-Global-Intelligence/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def gdelt_news():
    query = '(oil OR crude OR tanker OR shipping OR freight OR sanctions OR war OR pipeline OR refinery OR port OR OPEC OR LNG)'
    params = urllib.parse.urlencode({
        'query': query,
        'mode': 'artlist',
        'maxrecords': 40,
        'timespan': '1h',
        'sort': 'datedesc',
        'format': 'json'
    })
    raw = fetch_json('https://api.gdeltproject.org/api/v2/doc/doc?' + params)
    items = []
    seen = set()
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
    return {'source':'GDELT DOC 2.0','live':True,'items':items[:30]}

def eia_series(series_id):
    key = os.getenv('EIA_API_KEY','').strip()
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
    instruments = []
    source = []
    try:
        wti = latest_eia_value(eia_series('PET.RWTC.D'))
        brent = latest_eia_value(eia_series('PET.RBRTE.D'))
        if wti:
            instruments.append({'name':'WTI','price':wti['price'],'trend':'official EIA spot','signal_score':None,'period':wti['period'],'live':True})
        if brent:
            instruments.append({'name':'Brent','price':brent['price'],'trend':'official EIA spot','signal_score':None,'period':brent['period'],'live':True})
        if wti or brent:
            source.append('EIA')
    except Exception as exc:
        source.append('EIA error: ' + str(exc)[:120])
    for name in ['Gold','S&P 500','Bitcoin']:
        instruments.append({'name':name,'price':None,'trend':'data source not connected','signal_score':None,'live':False})
    return {'source':source or ['No market provider connected'],'instruments':instruments,'live':any(x.get('live') for x in instruments)}

def vessel_data():
    key = os.getenv('VESSELFINDER_API_KEY','').strip()
    imos = os.getenv('VESSELFINDER_IMOS','').strip()
    if not key:
        return {'source':'VesselFinder','live':False,'status':'API key not configured','items':[]}
    if not imos:
        return {'source':'VesselFinder','live':False,'status':'Set VESSELFINDER_IMOS secret with comma-separated IMO numbers','items':[]}
    url = 'https://api.vesselfinder.com/vessels?' + urllib.parse.urlencode({
        'userkey':key,'imo':imos,'extradata':'voyage,master','interval':60,'errormode':409
    })
    raw = fetch_json(url)
    items = []
    for v in raw if isinstance(raw, list) else []:
        ais = v.get('AIS',{})
        master = v.get('MASTERDATA',{})
        voyage = v.get('VOYAGE',{})
        items.append({
            'name':ais.get('NAME') or master.get('NAME') or 'Unknown vessel',
            'imo':ais.get('IMO') or master.get('IMO'),
            'vessel_type':master.get('TYPE') or ais.get('TYPE'),
            'flag':master.get('FLAG'),
            'lat':ais.get('LATITUDE'),'lon':ais.get('LONGITUDE'),
            'speed_knots':ais.get('SPEED'),'destination':ais.get('DESTINATION'),
            'eta':ais.get('ETA'),'last_port':voyage.get('LASTPORT'),
            'timestamp':ais.get('TIMESTAMP'),'live':True
        })
    return {'source':'VesselFinder AIS API','live':True,'status':'ok','items':items}

def derive_risk(news):
    text = ' '.join(i.get('headline','').lower() for i in news.get('items',[]))
    def count(words): return sum(text.count(w) for w in words)
    geo = min(100, 30 + count(['war','attack','missile','drone','sanction']) * 7)
    shipping = min(100, 25 + count(['tanker','shipping','vessel','port','strait','red sea']) * 6)
    oil = min(100, 25 + count(['oil','crude','pipeline','refinery','opec']) * 5)
    freight = min(100, 20 + count(['freight','shipping','port']) * 5)
    insurance = min(100, round((geo + shipping) / 2))
    scores = {'geopolitical':geo,'oil_supply':oil,'shipping':shipping,'freight':freight,'marine_insurance':insurance}
    return {'source':'PlaceOnUs derived from current headlines','live':True,'scores':scores,'method':'Transparent keyword-pressure heuristic; AI model not yet enabled.'}

def main():
    status = {'sources':{}}
    try:
        news = gdelt_news(); write('news.json', news); status['sources']['news']='ok'
    except Exception as exc:
        news = {'source':'GDELT','live':False,'items':[],'error':str(exc)}; write('news.json', news); status['sources']['news']='error'
    markets = market_data(); write('markets.json', markets); status['sources']['markets']='ok' if markets.get('live') else 'partial'
    vessels = vessel_data(); write('vessels.json', vessels); status['sources']['vessels']='ok' if vessels.get('live') else 'not_connected'
    risk = derive_risk(news); write('risk.json', risk)
    write('freight.json', {'source':'Not connected','live':False,'lanes':[],'status':'Licensed freight source required'})
    write('trends.json', {'source':'PlaceOnUs','live':False,'assets':[],'status':'Will activate after historical market feed is connected'})
    status['refresh_minutes']=10
    status['site']='news.placeonus.com'
    write('system-status.json', status)

if __name__ == '__main__':
    main()
