# SMTCD Trips API

Tiny Flask REST API serving SamTrans rider trip history.
Used by Salesforce as the backing store for the `Trip__x`
External Object via an Apex Custom Adapter.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
# Then in another shell:
curl 'http://localhost:8000/health'
curl 'http://localhost:8000/trips?rider_external_id=MARIE-001&limit=5'
```

## Deploy to Render

1. Push this repo to GitHub.
2. In Render, **New Web Service** → connect the repo.
3. Render auto-detects `render.yaml` (free plan, gunicorn, autoDeploy).
4. After build: visit `https://<your-service>.onrender.com/health`.

## Endpoints

| Method | Path                           | Purpose                          |
|-------:|---------------------------------|----------------------------------|
| GET    | `/health`                       | Liveness probe                   |
| GET    | `/riders`                       | List rider summary               |
| GET    | `/trips`                        | List/filter trips (paginated)    |
| GET    | `/trips/{trip_id}`              | Single trip by id                |

### `/trips` query params
- `rider_external_id` — exact match (e.g. `MARIE-001`)
- `agency` — `SamTrans`, `Caltrain`, `Redi-Wheels`
- `status` — `Completed`, `Delayed`, `Cancelled`, `No Show`, `Refunded`
- `route` — substring, case-insensitive
- `date_from`, `date_to` — `YYYY-MM-DD`
- `limit` — default 50, max 500
- `offset` — default 0
- `order_by` — `scheduled_departure_desc` (default) or `scheduled_departure_asc`

## Riders seeded

| External Id          | Name                  | Trips |
|----------------------|----------------------|-------|
| MARIE-001            | Marie Jane Williams  | 250   |
| MARIA-RODRIGUEZ-001  | Maria Rodriguez      | 180   |
| MARIA-LOPEZ-001      | Maria Lopez          | 95    |
| JAMES-CHEN-001       | James Chen           | 320   |
| AISHA-PATEL-001      | Aisha Patel          | 140   |

Real SamTrans, Caltrain, and Redi-Wheels routes are used.
Real Peninsula stops (Hillsdale, Daly City BART, Redwood City, etc.).
