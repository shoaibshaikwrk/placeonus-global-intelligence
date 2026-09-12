from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List, Optional
import os

app = FastAPI(title='PlaceOnUs Global Intelligence API', version='0.1.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

BASE = os.path.dirname(__file__)
STATIC = os.path.join(BASE, 'static')
app.mount('/static', StaticFiles(directory=STATIC), name='static')

class NewsItem(BaseModel):
    id: str
    headline: str
    source: str
    category: str
    region: str
    published_at: str
    summary: str
    market_impact: int
    oil_impact: int
    confidence: int
    url: Optional[str] = None

class Vessel(BaseModel):
    name: str
    vessel_type: str
    lat: float
    lon: float
    destination: str
    speed_knots: float
    route_risk: int
    insurance_risk: int

NEWS: List[NewsItem] = [
    NewsItem(id='n1', headline='Red Sea security conditions raise shipping-risk premium', source='Demo feed', category='GEOPOLITICS', region='Red Sea', published_at=datetime.now(timezone.utc).isoformat(), summary='Illustrative placeholder until live providers are configured.', market_impact=74, oil_impact=83, confidence=60),
    NewsItem(id='n2', headline='Energy markets monitor supply-route disruptions', source='Demo feed', category='ENERGY', region='Middle East', published_at=datetime.now(timezone.utc).isoformat(), summary='Illustrative placeholder until approved news and official energy feeds are connected.', market_impact=70, oil_impact=88, confidence=60),
    NewsItem(id='n3', headline='Freight markets price longer voyage distances', source='Demo feed', category='FREIGHT', region='Global', published_at=datetime.now(timezone.utc).isoformat(), summary='Illustrative placeholder until live freight providers are configured.', market_impact=61, oil_impact=42, confidence=55),
]

VESSELS = [
    Vessel(name='Demo Tanker 01', vessel_type='Crude Tanker', lat=25.1, lon=56.3, destination='Rotterdam', speed_knots=12.6, route_risk=91, insurance_risk=88),
    Vessel(name='Demo Container 07', vessel_type='Container', lat=13.4, lon=43.2, destination='Jeddah', speed_knots=15.1, route_risk=86, insurance_risk=79),
    Vessel(name='Demo LNG 03', vessel_type='LNG Carrier', lat=1.4, lon=104.0, destination='Singapore', speed_knots=14.4, route_risk=38, insurance_risk=32),
]

RISK_ZONES = [
    {'name':'Strait of Hormuz','lat':26.5,'lon':56.5,'geopolitical':96,'shipping':94,'insurance':92,'oil_supply':97},
    {'name':'Bab el-Mandeb','lat':12.6,'lon':43.4,'geopolitical':91,'shipping':95,'insurance':93,'oil_supply':82},
    {'name':'Suez Canal','lat':30.5,'lon':32.3,'geopolitical':74,'shipping':81,'insurance':72,'oil_supply':69},
    {'name':'Black Sea','lat':43.0,'lon':34.0,'geopolitical':84,'shipping':77,'insurance':85,'oil_supply':68},
    {'name':'Malacca Strait','lat':2.5,'lon':101.5,'geopolitical':35,'shipping':46,'insurance':34,'oil_supply':61},
]

@app.get('/')
def home():
    return FileResponse(os.path.join(STATIC, 'index.html'))

@app.get('/api/health')
def health():
    return {'status':'ok','service':'placeonus-intelligence','time':datetime.now(timezone.utc).isoformat()}

@app.get('/api/news')
def news(category: Optional[str] = None):
    items = NEWS if not category else [n for n in NEWS if n.category == category.upper()]
    return {'items':[n.model_dump() for n in items], 'live':False, 'message':'Configure providers in .env to enable live ingestion.'}

@app.get('/api/market-pulse')
def market_pulse():
    return {'live':False,'instruments':[
        {'symbol':'WTI','name':'WTI Crude','price':None,'change_pct':None,'trend':'BULLISH','signal_score':82},
        {'symbol':'BRENT','name':'Brent Crude','price':None,'change_pct':None,'trend':'BULLISH','signal_score':80},
        {'symbol':'SPX','name':'S&P 500','price':None,'change_pct':None,'trend':'NEUTRAL','signal_score':51},
        {'symbol':'GOLD','name':'Gold','price':None,'change_pct':None,'trend':'BULLISH','signal_score':68},
        {'symbol':'BTC','name':'Bitcoin','price':None,'change_pct':None,'trend':'NEUTRAL','signal_score':55},
    ]}

@app.get('/api/risk')
def risk():
    return {'updated_at':datetime.now(timezone.utc).isoformat(),'scores':{'geopolitical':86,'oil_supply':91,'shipping':88,'market_volatility':72,'economic':54,'freight':76,'marine_insurance':84},'regime':'RISK-OFF / HIGH VOLATILITY','confidence':81,'live':False}

@app.get('/api/vessels')
def vessels():
    return {'items':[v.model_dump() for v in VESSELS], 'live':False, 'provider':'demo'}

@app.get('/api/risk-zones')
def risk_zones():
    return {'items':RISK_ZONES}

@app.get('/api/freight')
def freight():
    return {'live':False,'lanes':[
        {'lane':'Asia → US West','rate':None,'unit':'USD/FEU','trend':'UP','risk':68},
        {'lane':'Asia → US East','rate':None,'unit':'USD/FEU','trend':'UP','risk':74},
        {'lane':'Shanghai → Rotterdam','rate':None,'unit':'USD/FEU','trend':'UP','risk':81},
        {'lane':'Middle East → Europe Tanker','rate':None,'unit':'Index','trend':'UP','risk':88},
    ]}

@app.get('/api/trends')
def trends():
    return {'live':False,'assets':[
        {'asset':'Oil','h1':'UP','d1':'UP','w1':'UP','score':82},
        {'asset':'Gold','h1':'UP','d1':'UP','w1':'FLAT','score':69},
        {'asset':'S&P 500','h1':'DOWN','d1':'FLAT','w1':'UP','score':51},
        {'asset':'USD','h1':'UP','d1':'UP','w1':'FLAT','score':64},
    ]}
