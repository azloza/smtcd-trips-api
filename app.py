"""
SMTCD Trips API
A small Flask service that fronts a SQLite trips store with REST endpoints
for the Salesforce External Object (Apex Custom Adapter) and a simple HTML
data-entry page.

Persistence:
  Trips live in a SQLite database (trips.db). On first boot, if the database
  is empty, it is seeded from trips.json (the original demo dataset). All reads
  and writes go through SQLite so every gunicorn worker sees the same data and
  a newly POSTed trip is immediately queryable by GET /trips (and therefore by
  the Salesforce SMTCD_Trip__x external object on its next query).

  NOTE: On Render's free tier the disk is ephemeral, so trips.db is reset on
  every deploy / cold-start. Submitted trips persist within a running instance
  but are NOT permanent on the free plan. Attach a persistent disk or move to
  Postgres for durable storage.

Endpoints:
  GET  /health                                 -> {ok: true}
  GET  /trips                                  -> all (paginated)
  GET  /trips?rider_external_id=MARIE-001      -> filtered
  GET  /trips?status=Delayed&agency=SamTrans   -> multi-filter
  GET  /trips/{trip_id}                        -> single trip
  POST /trips                                  -> create a trip (JSON or form)
  GET  /trip-entry                             -> HTML form to create a trip
  GET  /riders                                 -> distinct riders

Query params (on GET /trips):
  rider_external_id   exact match
  agency              exact match (SamTrans | Caltrain | Redi-Wheels)
  status              exact match
  route               substring (case-insensitive)
  date_from           ISO date (YYYY-MM-DD) -- scheduled_departure >= date_from
  date_to             ISO date (YYYY-MM-DD) -- scheduled_departure <= date_to
  limit               default 50, max 500
  offset              default 0
  order_by            scheduled_departure_desc (default) | scheduled_departure_asc
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, abort, render_template_string

app = Flask(__name__)
BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, "trips.json")
DB_FILE = os.environ.get("TRIPS_DB", os.path.join(BASE_DIR, "trips.db"))

MAX_LIMIT = 500
DEFAULT_LIMIT = 50

# Canonical trip field order — mirrors trips.json and the columns the Salesforce
# adapter (SMTCDTripsAdapter.parseTripsBody) reads back out.
TRIP_FIELDS = [
    "trip_id",
    "rider_external_id",
    "rider_name",
    "agency",
    "route",
    "vehicle",
    "origin_stop",
    "destination_stop",
    "scheduled_departure_utc",
    "scheduled_arrival_utc",
    "actual_departure_utc",
    "actual_arrival_utc",
    "delay_minutes",
    "status",
    "fare_cents",
    "payment_method",
    "notes",
]
INT_FIELDS = {"delay_minutes", "fare_cents"}
# Fields the data-entry form requires (the rest are optional / nullable).
REQUIRED_FIELDS = ["rider_external_id", "agency", "route", "status"]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the trips table if needed and seed from trips.json when empty."""
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trips (
                trip_id                 TEXT PRIMARY KEY,
                rider_external_id       TEXT,
                rider_name              TEXT,
                agency                  TEXT,
                route                   TEXT,
                vehicle                 TEXT,
                origin_stop             TEXT,
                destination_stop        TEXT,
                scheduled_departure_utc TEXT,
                scheduled_arrival_utc   TEXT,
                actual_departure_utc    TEXT,
                actual_arrival_utc      TEXT,
                delay_minutes           INTEGER,
                status                  TEXT,
                fare_cents              INTEGER,
                payment_method          TEXT,
                notes                   TEXT
            )
            """
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
        if count == 0 and os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                seed = json.load(f)
            rows = [tuple(t.get(k) for k in TRIP_FIELDS) for t in seed.get("trips", [])]
            if rows:
                placeholders = ",".join(["?"] * len(TRIP_FIELDS))
                conn.executemany(
                    f"INSERT OR IGNORE INTO trips ({','.join(TRIP_FIELDS)}) "
                    f"VALUES ({placeholders})",
                    rows,
                )
                conn.commit()
    finally:
        conn.close()


def row_to_trip(row):
    return {k: row[k] for k in TRIP_FIELDS}


def next_trip_id(conn):
    """Generate the next TRP-NNNNN id based on the current max numeric suffix."""
    rows = conn.execute("SELECT trip_id FROM trips").fetchall()
    mx = 0
    for r in rows:
        m = re.match(r"^TRP-(\d+)$", r["trip_id"] or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"TRP-{mx + 1:05d}"


def rider_name_for(conn, rider_external_id):
    """Best-effort rider_name lookup from existing trips for the same rider id."""
    row = conn.execute(
        "SELECT rider_name FROM trips WHERE rider_external_id = ? "
        "AND rider_name IS NOT NULL AND rider_name != '' LIMIT 1",
        (rider_external_id,),
    ).fetchone()
    return row["rider_name"] if row else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def build_filtered_query():
    """Builds a parameterized WHERE clause from the request query string."""
    q = request.args
    clauses, params = [], []

    rider = q.get("rider_external_id")
    if rider:
        clauses.append("rider_external_id = ?")
        params.append(rider)
    agency = q.get("agency")
    if agency:
        clauses.append("agency = ?")
        params.append(agency)
    status = q.get("status")
    if status:
        clauses.append("status = ?")
        params.append(status)
    route = q.get("route")
    if route:
        clauses.append("LOWER(route) LIKE ?")
        params.append(f"%{route.lower()}%")
    date_from = q.get("date_from")
    if date_from:
        clauses.append("substr(scheduled_departure_utc, 1, 10) >= ?")
        params.append(date_from)
    date_to = q.get("date_to")
    if date_to:
        clauses.append("substr(scheduled_departure_utc, 1, 10) <= ?")
        params.append(date_to)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    conn = get_db()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
    finally:
        conn.close()
    return jsonify({
        "ok": True,
        "service": "smtcd-trips-api",
        "trip_count": n,
        "storage": "sqlite",
    })


@app.get("/riders")
def riders():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT rider_external_id, rider_name, COUNT(*) AS trip_count "
            "FROM trips GROUP BY rider_external_id, rider_name "
            "ORDER BY trip_count DESC"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"riders": [
        {
            "rider_external_id": r["rider_external_id"],
            "rider_name": r["rider_name"],
            "trip_count": r["trip_count"],
        }
        for r in rows
    ]})


@app.get("/trips")
def list_trips():
    limit = parse_int("limit", DEFAULT_LIMIT, mx=MAX_LIMIT)
    offset = parse_int("offset", 0)
    order_by = request.args.get("order_by", "scheduled_departure_desc")
    direction = "ASC" if order_by == "scheduled_departure_asc" else "DESC"

    where, params = build_filtered_query()

    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM trips{where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM trips{where} "
            f"ORDER BY scheduled_departure_utc {direction} "
            f"LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()

    page = [row_to_trip(r) for r in rows]
    return jsonify({
        "total_matched": total,
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        "trips": page,
    })


@app.get("/trips/<trip_id>")
def get_trip(trip_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404, description=f"trip_id '{trip_id}' not found")
    return jsonify(row_to_trip(row))


# ---------------------------------------------------------------------------
# Write endpoint
# ---------------------------------------------------------------------------
def coerce_payload(src):
    """Normalizes an incoming JSON/form payload into a trip dict."""
    trip = {}
    for k in TRIP_FIELDS:
        if k == "trip_id":
            continue
        val = src.get(k)
        if isinstance(val, str):
            val = val.strip()
            if val == "":
                val = None
        trip[k] = val

    # Integer coercion for numeric fields.
    for k in INT_FIELDS:
        if trip.get(k) is not None:
            try:
                trip[k] = int(trip[k])
            except (TypeError, ValueError):
                abort(400, description=f"{k} must be an integer")
    return trip


def create_trip(payload):
    """Validates, assigns a trip_id, inserts, and returns the new trip dict."""
    trip = coerce_payload(payload)

    missing = [f for f in REQUIRED_FIELDS if not trip.get(f)]
    if missing:
        abort(400, description=f"Missing required field(s): {', '.join(missing)}")

    conn = get_db()
    try:
        trip["trip_id"] = next_trip_id(conn)
        if not trip.get("rider_name"):
            trip["rider_name"] = rider_name_for(conn, trip["rider_external_id"])

        cols = TRIP_FIELDS
        placeholders = ",".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO trips ({','.join(cols)}) VALUES ({placeholders})",
            tuple(trip.get(c) for c in cols),
        )
        conn.commit()
    finally:
        conn.close()
    return trip


@app.post("/trips")
def post_trip():
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.form.to_dict()

    trip = create_trip(payload)

    # Browser form submit -> redirect back to the entry page with a success note.
    if not request.is_json and request.form:
        return render_template_string(
            ENTRY_PAGE, fields=FORM_FIELDS, created=trip, error=None, values={}
        )
    return jsonify(trip), 201


# ---------------------------------------------------------------------------
# HTML data-entry page
# ---------------------------------------------------------------------------
# (label, name, input_type, required, placeholder/help)
FORM_FIELDS = [
    ("Rider External Id", "rider_external_id", "text", True, "e.g. MARIE-001"),
    ("Rider Name", "rider_name", "text", False, "auto-filled if rider known"),
    ("Agency", "agency", "select", True, ["SamTrans", "Caltrain", "Redi-Wheels"]),
    ("Route", "route", "text", True, "e.g. ECR - Daly City to Palo Alto"),
    ("Vehicle", "vehicle", "text", False, "e.g. BUS-2103"),
    ("Origin Stop", "origin_stop", "text", False, "e.g. Daly City BART"),
    ("Destination Stop", "destination_stop", "text", False, "e.g. Redwood City"),
    ("Scheduled Departure (UTC)", "scheduled_departure_utc", "datetime-local", False, ""),
    ("Scheduled Arrival (UTC)", "scheduled_arrival_utc", "datetime-local", False, ""),
    ("Actual Departure (UTC)", "actual_departure_utc", "datetime-local", False, ""),
    ("Actual Arrival (UTC)", "actual_arrival_utc", "datetime-local", False, ""),
    ("Delay (minutes)", "delay_minutes", "number", False, "e.g. 4"),
    ("Status", "status", "select", True,
     ["Completed", "Delayed", "Cancelled", "No Show", "Refunded"]),
    ("Fare (cents)", "fare_cents", "number", False, "e.g. 450"),
    ("Payment Method", "payment_method", "text", False, "e.g. Clipper Card"),
    ("Notes", "notes", "textarea", False, ""),
]

ENTRY_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMTCD Trip Entry</title>
<style>
  :root { --navy:#00355C; --blue:#00529B; --red:#E2383F; --line:#E2E8F0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
         background:#F4F6F8; color:#1F2937; }
  header { background:linear-gradient(180deg,var(--blue) 0%,var(--navy) 100%);
           color:#fff; padding:18px 24px; border-bottom:3px solid var(--red); }
  header h1 { margin:0; font-size:18px; letter-spacing:.02em; }
  header p { margin:4px 0 0; font-size:12.5px; opacity:.9; }
  main { max-width:860px; margin:24px auto; padding:0 16px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:12px;
          padding:24px; box-shadow:0 1px 4px rgba(0,0,0,.06); }
  .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; }
  @media (max-width:640px){ .grid{ grid-template-columns:1fr; } }
  label { display:flex; flex-direction:column; gap:6px; font-size:13px; font-weight:600; }
  .req { color:var(--red); }
  input, select, textarea { font:inherit; padding:9px 11px; border:1px solid #CBD5E1;
          border-radius:8px; background:#fff; width:100%; }
  textarea { min-height:70px; resize:vertical; }
  .full { grid-column:1 / -1; }
  .actions { margin-top:20px; display:flex; gap:12px; align-items:center; }
  button { background:var(--blue); color:#fff; border:none; border-radius:999px;
           padding:11px 22px; font-weight:700; cursor:pointer; }
  button:hover { background:var(--navy); }
  .banner { padding:12px 14px; border-radius:8px; margin-bottom:18px; font-size:14px; }
  .ok { background:#ECFDF5; border:1px solid #6EE7B7; color:#065F46; }
  .err { background:#FEF2F2; border:1px solid #FCA5A5; color:#991B1B; }
  .hint { font-weight:400; color:#6B7280; font-size:12px; }
  code { background:#F1F5F9; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>SMTCD Trip Entry</h1>
  <p>Add a trip to the SamTrans Trips API &middot; appears in Salesforce via the SMTCD Trip external object</p>
</header>
<main>
  {% if created %}
    <div class="banner ok">
      Trip <strong>{{ created.trip_id }}</strong> created for
      <strong>{{ created.rider_external_id }}</strong>
      ({{ created.agency }} &middot; {{ created.route }}).
    </div>
  {% endif %}
  {% if error %}
    <div class="banner err">{{ error }}</div>
  {% endif %}

  <div class="card">
    <form method="POST" action="/trips">
      <div class="grid">
        {% for label, name, itype, required, extra in fields %}
          <label class="{{ 'full' if itype in ['textarea'] else '' }}">
            <span>{{ label }}{% if required %}<span class="req"> *</span>{% endif %}</span>
            {% if itype == 'select' %}
              <select name="{{ name }}" {{ 'required' if required else '' }}>
                <option value="">Select…</option>
                {% for opt in extra %}
                  <option value="{{ opt }}" {{ 'selected' if values.get(name)==opt else '' }}>{{ opt }}</option>
                {% endfor %}
              </select>
            {% elif itype == 'textarea' %}
              <textarea name="{{ name }}" placeholder="{{ extra }}">{{ values.get(name, '') }}</textarea>
            {% else %}
              <input type="{{ itype }}" name="{{ name }}"
                     {{ 'required' if required else '' }}
                     value="{{ values.get(name, '') }}"
                     {% if itype not in ['number','datetime-local'] %}placeholder="{{ extra }}"{% endif %}>
            {% endif %}
          </label>
        {% endfor %}
      </div>
      <div class="actions">
        <button type="submit">Create trip</button>
        <span class="hint"><code>trip_id</code> is generated automatically (TRP-NNNNN).</span>
      </div>
    </form>
  </div>
</main>
</body>
</html>
"""


@app.get("/trip-entry")
def trip_entry():
    return render_template_string(
        ENTRY_PAGE, fields=FORM_FIELDS, created=None, error=None, values={}
    )


# ---------------------------------------------------------------------------
# Read-only "all records" view (for showing the client every trip at a glance)
# ---------------------------------------------------------------------------
# Columns shown in the all-records table: (header label, trip field key).
VIEW_COLUMNS = [
    ("Trip Id", "trip_id"),
    ("Rider Id", "rider_external_id"),
    ("Rider", "rider_name"),
    ("Agency", "agency"),
    ("Route", "route"),
    ("Origin", "origin_stop"),
    ("Destination", "destination_stop"),
    ("Departure (UTC)", "scheduled_departure_utc"),
    ("Delay (min)", "delay_minutes"),
    ("Status", "status"),
    ("Fare", "fare_cents"),
    ("Payment", "payment_method"),
]

VIEW_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMTCD Trips — All Records</title>
<style>
  :root { --navy:#00355C; --blue:#00529B; --red:#E2383F; --line:#E2E8F0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
         background:#F4F6F8; color:#1F2937; }
  header { background:linear-gradient(180deg,var(--blue) 0%,var(--navy) 100%);
           color:#fff; padding:18px 24px; border-bottom:3px solid var(--red); }
  header h1 { margin:0; font-size:18px; letter-spacing:.02em; }
  header p { margin:4px 0 0; font-size:12.5px; opacity:.9; }
  main { max-width:1280px; margin:24px auto; padding:0 16px; }
  .meta { display:flex; align-items:baseline; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
  .count { font-size:15px; font-weight:700; color:var(--navy); }
  .count span { color:var(--blue); }
  .sub { font-size:12.5px; color:#6B7280; }
  .card { background:#fff; border:1px solid var(--line); border-radius:12px;
          overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.06); }
  .tablewrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:13px; white-space:nowrap; }
  thead th { background:#F1F5F9; color:var(--navy); text-align:left;
             padding:11px 14px; border-bottom:2px solid var(--line);
             position:sticky; top:0; font-weight:700; }
  tbody td { padding:10px 14px; border-bottom:1px solid var(--line); }
  tbody tr:nth-child(even) { background:#FAFBFC; }
  tbody tr:hover { background:#EFF6FF; }
  .pill { display:inline-block; padding:2px 10px; border-radius:999px;
          font-size:11.5px; font-weight:700; }
  .s-Completed { background:#ECFDF5; color:#065F46; }
  .s-Delayed   { background:#FFFBEB; color:#92400E; }
  .s-Cancelled { background:#FEF2F2; color:#991B1B; }
  .s-No.Show, .s-NoShow { background:#F3F4F6; color:#374151; }
  .s-Refunded  { background:#EEF2FF; color:#3730A3; }
  .muted { color:#9CA3AF; }
  footer { max-width:1280px; margin:14px auto 32px; padding:0 16px;
           font-size:12px; color:#6B7280; }
  code { background:#F1F5F9; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>SMTCD Trips — All Records</h1>
  <p>Live read-only view of every trip in the SamTrans Trips API &middot; source of the Salesforce SMTCD Trip external object</p>
</header>
<main>
  <div class="meta">
    <div class="count"><span>{{ total }}</span> trip record{{ '' if total == 1 else 's' }}</div>
    <div class="sub">Pulled live from the trips store &middot; ordered by most recent departure</div>
  </div>
  <div class="card">
    <div class="tablewrap">
      <table>
        <thead>
          <tr>{% for label, key in columns %}<th>{{ label }}</th>{% endfor %}</tr>
        </thead>
        <tbody>
          {% for t in trips %}
          <tr>
            {% for label, key in columns %}
              {% set val = t.get(key) %}
              {% if key == 'status' and val %}
                <td><span class="pill s-{{ val|replace(' ','') }}">{{ val }}</span></td>
              {% elif key == 'fare_cents' and val is not none %}
                <td>${{ '%.2f'|format(val / 100.0) }}</td>
              {% elif val is none or val == '' %}
                <td class="muted">&mdash;</td>
              {% else %}
                <td>{{ val }}</td>
              {% endif %}
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</main>
<footer>
  Read-only. To add a trip use <code>/trip-entry</code>. JSON for the same data: <code>/trips</code>.
</footer>
</body>
</html>
"""


@app.get("/trips-view")
def trips_view():
    """Read-only HTML table of all trips (newest first). For client demos."""
    where, params = build_filtered_query()
    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM trips{where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM trips{where} ORDER BY scheduled_departure_utc DESC",
            params,
        ).fetchall()
    finally:
        conn.close()
    trips = [row_to_trip(r) for r in rows]
    return render_template_string(
        VIEW_PAGE, columns=VIEW_COLUMNS, trips=trips, total=total
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(400)
def _400(e):
    if request.path == "/trips" and request.method == "POST" and request.form:
        # Re-render the form with the error and the user's entered values.
        return render_template_string(
            ENTRY_PAGE, fields=FORM_FIELDS, created=None,
            error=str(e.description), values=request.form.to_dict()
        ), 400
    return jsonify({"error": "bad_request", "message": str(e.description)}), 400


@app.errorhandler(404)
def _404(e):
    return jsonify({"error": "not_found", "message": str(e.description)}), 404


# Seed the database on import (so it runs under gunicorn too, not just __main__).
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
