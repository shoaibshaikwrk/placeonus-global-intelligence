# PlaceOnUs Global Intelligence

A standalone global intelligence platform designed to power `news.placeonus.com`.

## Visual system
This project intentionally matches the main PlaceOnUs website:
- Manrope for display text
- Source Sans 3 for body text
- White and `#F6F8FA` surfaces
- `#111418` / `#1F2328` primary text
- PlaceOnUs blue `#1E6FD9` accent
- Rounded cards, pill controls, subtle borders and the same sticky navigation treatment

## Included
- News intelligence cards with reported facts separated from AI analysis
- Market pulse
- Global risk dashboard
- Freight pressure and route risk
- Vessel watchlist
- Marine insurance exposure
- Trend engine
- Interactive Leaflet world map with shipping risk zones and vessel markers
- Mobile navigation and dashboard tabs
- FastAPI JSON endpoints for each dashboard module
- Demo/live labeling so placeholder data is never presented as real-time data

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000`.

## Next integrations
- Official/approved breaking-news and geopolitical feeds
- EIA / OPEC / IEA energy data
- Live market prices and historical bars
- Freight indices and route rates
- AIS vessel positions and voyage information
- Port congestion
- Marine insurance, sanctions and piracy risk data

Use official APIs, RSS feeds, open data, or properly licensed feeds. Do not republish paywalled publisher content without permission.
