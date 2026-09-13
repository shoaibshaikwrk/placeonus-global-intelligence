import json
from pathlib import Path

TRADE = Path('data/trade.json')

FOCUS_COUNTRIES = {
    '842': 'United States',
    '356': 'India',
    '682': 'Saudi Arabia',
    '156': 'China',
    '784': 'United Arab Emirates',
    '392': 'Japan',
    '410': 'South Korea',
    '276': 'Germany',
}

WHY = {
    '842': 'U.S. markets, energy demand, technology and imports',
    '356': 'high-growth demand, energy imports and manufacturing',
    '682': 'oil, OPEC and energy-supply signals',
    '156': 'global manufacturing, commodities and demand',
    '784': 'Gulf trade, logistics, shipping and energy',
    '392': 'autos, industrials, yen-sensitive trade and LNG',
    '410': 'semiconductors, shipbuilding, LNG and exports',
    '276': 'European industrial, automotive and manufacturing signals',
}


def main():
    if not TRADE.exists():
        return
    data = json.loads(TRADE.read_text(encoding='utf-8'))
    countries = data.get('countries') or []
    countries = [c for c in countries if str(c.get('code')) in FOCUS_COUNTRIES]
    order = {code: i for i, code in enumerate(FOCUS_COUNTRIES)}
    countries.sort(key=lambda c: order.get(str(c.get('code')), 999))
    data['countries'] = countries
    data['focus_only'] = True
    data['focus_countries'] = [
        {'code': code, 'name': name, 'analysis_role': WHY[code]}
        for code, name in FOCUS_COUNTRIES.items()
    ]
    data['status'] = (
        'Focused trade intelligence for the United States, India, Saudi Arabia, China, UAE, '
        'Japan, South Korea and Germany. Selected for high-value market, energy, manufacturing '
        'and shipping analysis.'
    )
    TRADE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
