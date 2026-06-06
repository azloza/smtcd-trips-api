"""
SamTrans Trips Data Generator
Generates realistic trip history JSON for 5 riders using real SamTrans, Caltrain,
and Redi-Wheels routes and real stops along the SF Peninsula corridor.

Usage:  python3 generate_trips.py > trips.json
"""
import json, random
from datetime import datetime, timedelta, timezone

random.seed(42)  # deterministic

# Real riders (match the Salesforce Contact external ids we'll set)
RIDERS = [
    {
        "rider_external_id": "MARIE-001",
        "rider_name": "Marie Jane Williams",
        "home_corridor": "Hillsdale Caltrain Station",
        "work_corridor": "Palo Alto Transit Center",
        "trips_target": 12,
        "primary_agency": "SamTrans",
    },
    {
        "rider_external_id": "MARIA-RODRIGUEZ-001",
        "rider_name": "Maria Rodriguez",
        "home_corridor": "Daly City BART Station",
        "work_corridor": "San Francisco - Mission St",
        "trips_target": 8,
        "primary_agency": "SamTrans",
    },
    {
        "rider_external_id": "MARIA-LOPEZ-001",
        "rider_name": "Maria Lopez",
        "home_corridor": "Redwood City Caltrain Station",
        "work_corridor": "Disability Rights California",
        "trips_target": 6,
        "primary_agency": "Redi-Wheels",
    },
    {
        "rider_external_id": "JAMES-CHEN-001",
        "rider_name": "James Chen",
        "home_corridor": "San Mateo Caltrain Station",
        "work_corridor": "San Francisco - Embarcadero",
        "trips_target": 4,
        "primary_agency": "Caltrain",
    },
    {
        "rider_external_id": "AISHA-PATEL-001",
        "rider_name": "Aisha Patel",
        "home_corridor": "Belmont Caltrain Station",
        "work_corridor": "College of San Mateo",
        "trips_target": 4,
        "primary_agency": "SamTrans",
    },
]

# Real routes from the org (matched to agency)
ROUTES_BY_AGENCY = {
    "SamTrans": [
        "El Camino Real Rapid (Daly City - Palo Alto)",
        "Hillsdale - San Francisco via El Camino",
        "Redwood City - SF via Bayshore (Express)",
        "Hillsdale - College of San Mateo",
        "Hillsdale - Foster City",
        "San Mateo - Redwood Shores",
        "Daly City BART - Palo Alto Transit Center",
        "Redwood City - Belmont (Tower Road)",
    ],
    "Caltrain": [
        "Local Southbound",
        "Local Northbound",
        "Limited Southbound",
        "Limited Northbound",
        "Bullet Southbound",
        "Bullet Northbound",
        "Baby Bullet Southbound",
        "Baby Bullet Northbound",
        "Weekend Service",
    ],
    "Redi-Wheels": [
        "Redi-Wheels Paratransit (On-Demand ADA)",
    ],
}

# Real stops along the Peninsula corridor (SamTrans + Caltrain + key destinations)
STOPS = [
    "Daly City BART Station",
    "Colma BART Station",
    "South San Francisco Caltrain Station",
    "San Bruno Caltrain Station",
    "Millbrae Caltrain / BART Station",
    "Broadway Caltrain Station (Burlingame)",
    "Burlingame Caltrain Station",
    "San Mateo Caltrain Station",
    "Hayward Park Caltrain Station",
    "Hillsdale Caltrain Station",
    "Belmont Caltrain Station",
    "San Carlos Caltrain Station",
    "Redwood City Caltrain Station",
    "Atherton Caltrain Station",
    "Menlo Park Caltrain Station",
    "Palo Alto Transit Center",
    "California Avenue Caltrain Station",
    "San Antonio Caltrain Station",
    "Mountain View Caltrain Station",
    "College of San Mateo",
    "Foster City Transit Center",
    "Redwood Shores Plaza",
    "Stanford Health Care - 300 Pasteur Dr",
    "Kaiser Permanente Redwood City",
    "Sequoia Hospital",
    "Veterans Transportation Services",
    "San Francisco - 4th & King Caltrain",
    "San Francisco - Embarcadero",
    "San Francisco - Mission St",
    "Disability Rights California",
    "Senior Mobility Programs",
    "Center for Independent Living (CIL)",
]

VEHICLES_BY_AGENCY = {
    "SamTrans":     [f"BUS-{n:04d}" for n in range(2100, 2160)],
    "Caltrain":     [f"TRN-{n:03d}" for n in range(101, 130)],
    "Redi-Wheels":  [f"RDW-{n:03d}" for n in range(401, 425)],
}

# Realistic fare bands (cents) by agency
FARE_RANGES = {
    "SamTrans":     [225, 225, 225, 225, 110, 110, 450],  # local + reduced + zone-2
    "Caltrain":     [400, 400, 525, 525, 650, 875, 1075, 200],
    "Redi-Wheels":  [450, 450, 450, 250],  # ADA fare flat
}

STATUS_WEIGHTS = [
    ("Completed", 88),
    ("Completed", 88),
    ("Delayed", 6),
    ("Cancelled", 3),
    ("No Show", 2),
    ("Refunded", 1),
]

PAYMENT_METHODS = [
    "Clipper Card", "Clipper Card", "Clipper Card", "Clipper Card",
    "Cash", "Cash",
    "Mobile Pay (Apple Pay)",
    "Mobile Pay (Google Pay)",
    "SamTrans Monthly Pass",
    "Redi-Wheels Voucher",
]

def weighted_choice(pairs):
    items, weights = zip(*pairs)
    return random.choices(items, weights=weights, k=1)[0]

def pick_route(agency):
    return random.choice(ROUTES_BY_AGENCY[agency])

def pick_corridor_stops(rider):
    """Most trips connect rider home <-> work, with some leisure stops mixed in."""
    if random.random() < 0.68:
        return rider["home_corridor"], rider["work_corridor"]
    elif random.random() < 0.5:
        return rider["work_corridor"], rider["home_corridor"]
    else:
        o, d = random.sample(STOPS, 2)
        return o, d

def iso_minus(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

def generate_for_rider(rider, start_trip_id):
    trips = []
    n = rider["trips_target"]
    primary = rider["primary_agency"]
    secondary_pool = [a for a in ROUTES_BY_AGENCY.keys() if a != primary]
    for i in range(n):
        trip_id = start_trip_id + i

        # 85% primary agency, 15% other (transfer/multimodal)
        agency = primary if random.random() < 0.85 else random.choice(secondary_pool)

        route = pick_route(agency)
        origin, dest = pick_corridor_stops(rider)
        vehicle = random.choice(VEHICLES_BY_AGENCY[agency])

        # Spread trips over the last 120 days
        days_ago = random.randint(0, 120)
        hour = random.choices(
            [7,8,9,10,11,12,13,14,15,16,17,18,19,20],
            weights=[10,18,8,4,4,4,5,5,5,12,18,12,5,3], k=1)[0]
        minute = random.randint(0, 59)
        sched_dep = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=random.randint(0,3))
        sched_dep = sched_dep.replace(hour=hour, minute=minute, second=0, microsecond=0)

        trip_duration_min = random.choice([12, 18, 22, 27, 35, 42, 55, 75])
        sched_arr = sched_dep + timedelta(minutes=trip_duration_min)

        status = weighted_choice(STATUS_WEIGHTS)
        delay_min = 0
        actual_dep = sched_dep
        actual_arr = sched_arr

        if status == "Delayed":
            delay_min = random.choice([4, 6, 9, 12, 18, 25])
            actual_dep = sched_dep + timedelta(minutes=delay_min)
            actual_arr = sched_arr + timedelta(minutes=delay_min)
        elif status == "Cancelled" or status == "No Show":
            actual_dep = None
            actual_arr = None

        fare = random.choice(FARE_RANGES[agency])
        payment = random.choice(PAYMENT_METHODS)

        trips.append({
            "trip_id": f"TRP-{trip_id:05d}",
            "rider_external_id": rider["rider_external_id"],
            "rider_name": rider["rider_name"],
            "agency": agency,
            "route": route,
            "vehicle": vehicle,
            "origin_stop": origin,
            "destination_stop": dest,
            "scheduled_departure_utc": sched_dep.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scheduled_arrival_utc":   sched_arr.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actual_departure_utc":    actual_dep.strftime("%Y-%m-%dT%H:%M:%SZ") if actual_dep else None,
            "actual_arrival_utc":      actual_arr.strftime("%Y-%m-%dT%H:%M:%SZ") if actual_arr else None,
            "delay_minutes": delay_min,
            "status": status,
            "fare_cents": fare,
            "payment_method": payment,
            "notes": None,
        })
    return trips

def main():
    all_trips = []
    next_id = 1
    for rider in RIDERS:
        rider_trips = generate_for_rider(rider, next_id)
        next_id += len(rider_trips)
        all_trips.extend(rider_trips)

    # Sort newest first
    all_trips.sort(key=lambda t: t["scheduled_departure_utc"], reverse=True)

    out = {
        "_meta": {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_trips": len(all_trips),
            "riders": [
                {"rider_external_id": r["rider_external_id"], "rider_name": r["rider_name"], "trip_count": r["trips_target"]}
                for r in RIDERS
            ]
        },
        "trips": all_trips,
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
