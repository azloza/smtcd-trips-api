"""
SMTCD Trips API
A small Flask service that fronts trips.json with REST endpoints
for the Salesforce External Object (Apex Custom Adapter).

Endpoints:
  GET  /health                                 -> {ok: true}
  GET  /trips                                  -> all (paginated)
  GET  /trips?rider_external_id=MARIE-001      -> filtered
  GET  /trips?status=Delayed&agency=SamTrans   -> multi-filter
  GET  /trips/{trip_id}                        -> single trip
  GET  /riders                                 -> distinct riders

Query params (on /trips):
  rider_external_id   exact match
  agency              exact match (SamTrans | Caltrain | Redi-Wheels)
  status              exact match
  route               substring (case-insensitive)
  date_from           ISO date (YYYY-MM-DD) -- scheduled_departure >= date_from
  date_to             ISO date (YYYY-MM-DD) -- scheduled_departure <  date_to + 1day
  limit               default 50, max 500
  offset              default 0
  order_by            scheduled_departure_desc (default) | scheduled_departure_asc
"""
import json, os
from datetime import datetime
from flask import Flask, request, jsonify, abort

app = Flask(__name__)
DATA_FILE = os.path.join(os.path.dirname(__file__), "trips.json")

# Load once at startup (read-mostly; tiny payload)
with open(DATA_FILE, "r") as f:
    DB = json.load(f)
TRIPS = DB["trips"]
META  = DB["_meta"]

MAX_LIMIT = 500
DEFAULT_LIMIT = 50

def parse_int(name, default, mx=None):
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        abort(400, description=f"{name} must be an integer")
    if mx is not None and v > mx:
        v = mx
    return max(v, 0)

def matches(trip):
    q = request.args
    rider = q.get("rider_external_id")
    if rider and trip["rider_external_id"] != rider:
        return False
    agency = q.get("agency")
    if agency and trip["agency"] != agency:
        return False
    status = q.get("status")
    if status and trip["status"] != status:
        return False
    route = q.get("route")
    if route and route.lower() not in (trip["route"] or "").lower():
        return False
    date_from = q.get("date_from")
    if date_from and trip["scheduled_departure_utc"][:10] < date_from:
        return False
    date_to = q.get("date_to")
    if date_to and trip["scheduled_departure_utc"][:10] > date_to:
        return False
    return True

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "smtcd-trips-api",
        "trip_count": len(TRIPS),
        "generated_at_utc": META.get("generated_at_utc"),
    })

@app.get("/riders")
def riders():
    return jsonify({"riders": META["riders"]})

@app.get("/trips")
def list_trips():
    limit = parse_int("limit", DEFAULT_LIMIT, mx=MAX_LIMIT)
    offset = parse_int("offset", 0)
    order_by = request.args.get("order_by", "scheduled_departure_desc")

    filtered = [t for t in TRIPS if matches(t)]

    if order_by == "scheduled_departure_asc":
        filtered.sort(key=lambda t: t["scheduled_departure_utc"])
    else:
        filtered.sort(key=lambda t: t["scheduled_departure_utc"], reverse=True)

    page = filtered[offset:offset+limit]
    return jsonify({
        "total_matched": len(filtered),
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        "trips": page,
    })

@app.get("/trips/<trip_id>")
def get_trip(trip_id):
    for t in TRIPS:
        if t["trip_id"] == trip_id:
            return jsonify(t)
    abort(404, description=f"trip_id '{trip_id}' not found")

@app.errorhandler(400)
def _400(e):
    return jsonify({"error": "bad_request", "message": str(e.description)}), 400

@app.errorhandler(404)
def _404(e):
    return jsonify({"error": "not_found", "message": str(e.description)}), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
