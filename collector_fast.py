import asyncio
import json
from pathlib import Path

import collector

DATA = Path('data')


def preserve_news_on_error(exc):
    previous = collector.read_existing('news.json', {'source': 'GDELT', 'live': False, 'items': []}) or {'source': 'GDELT', 'live': False, 'items': []}
    previous['live'] = False
    previous['stale'] = bool(previous.get('items'))
    previous['error'] = str(exc)
    collector.write('news.json', previous)
    return previous


def main():
    status = {'sources': {}, 'refresh_minutes': 10, 'site': 'news.placeonus.com', 'mode': 'fast'}

    try:
        news = collector.gdelt_news()
        collector.write('news.json', news)
        status['sources']['news'] = 'ok'
    except Exception as exc:
        news = preserve_news_on_error(exc)
        status['sources']['news'] = 'stale' if news.get('items') else 'error'

    try:
        markets = collector.market_data()
        collector.write('markets.json', markets)
        status['sources']['markets'] = 'ok' if markets.get('live') else 'partial'
    except Exception as exc:
        status['sources']['markets'] = 'error'
        status['market_error'] = str(exc)[:180]

    try:
        vessels = asyncio.run(collector.collect_aisstream(duration_seconds=20, max_vessels=200))
        collector.write('vessels.json', vessels)
        status['sources']['vessels'] = 'ok' if vessels.get('live') else 'connected_no_positions' if vessels.get('subscription_confirmed') else 'error'
    except Exception as exc:
        status['sources']['vessels'] = 'error'
        status['vessel_error'] = str(exc)[:180]

    try:
        collector.write('risk.json', collector.derive_risk(news))
    except Exception as exc:
        status['risk_error'] = str(exc)[:180]

    # Trade is refreshed separately by the bounded Census enrichment step.
    trade = collector.read_existing('trade.json', {}) or {}
    status['sources']['trade'] = 'ok' if trade.get('countries') else 'stale'

    collector.write('system-status.json', status)


if __name__ == '__main__':
    main()
