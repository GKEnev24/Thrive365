"""
Routing & geocoding services for the Thrive365 map.

Two providers:
- OpenRouteService (ORS) — directions for walking / cycling / driving with
  optional safety profiles (sidewalk preference, lit-street awareness via
  `extra_info`). Requires ORS_API_KEY in the environment.
- Nominatim — OSM's free geocoder, used for address → coords lookup. Required
  by their usage policy: identifying User-Agent, no high-volume queries.

Every public function in this module is safe to call when the relevant key
isn't set — it returns a structured error dict rather than raising, so the
HTTP layer can pass a clean JSON response straight through to the client.
"""

import os
import math
import requests

ORS_BASE = 'https://api.openrouteservice.org'
NOMINATIM_BASE = 'https://nominatim.openstreetmap.org'

# Burgas bounding box, used as a viewbox hint for the geocoder so "Александровска"
# (a common street name in many BG cities) resolves locally first.
BURGAS_VIEWBOX = (27.30, 42.40, 27.65, 42.70)  # lon_min, lat_min, lon_max, lat_max

# ORS profile per mode + preference. ORS exposes one profile per surface type;
# the "preference" toggle (fast/safe/scenic) maps to `preference` + options.
_PROFILES = {
    'walk':  'foot-walking',
    'bike':  'cycling-regular',
    'drive': 'driving-car',
}
_SAFE_PROFILES = {
    'walk':  'foot-walking',           # ORS pedestrian already prefers sidewalks
    'bike':  'cycling-safe',           # explicit "safe" cycling profile
    'drive': 'driving-car',
}


def _ors_key():
    return (os.environ.get('ORS_API_KEY') or '').strip()


def _nominatim_user_agent():
    return os.environ.get('NOMINATIM_USER_AGENT',
                          'Thrive365/1.0 (contact@thrive365.bg)').strip()


def _haversine_m(a, b):
    """Approximate distance in metres between two (lat, lng) points."""
    lat1, lng1 = a; lat2, lng2 = b
    R = 6371000.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lng2 - lng1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(h))


# ── Routing ──────────────────────────────────────────────────────────────────

def route(origin, destination, mode='walk', prefer='fast', waypoints=None):
    """Request a route from origin to destination through optional waypoints.

    Args:
        origin, destination: (lat, lng) tuples.
        mode: 'walk' | 'bike' | 'drive'.
        prefer: 'fast' | 'safe' | 'scenic'.
        waypoints: optional list of (lat, lng) intermediate stops.

    Returns a dict:
        {ok: True, geometry: [[lat,lng], ...], distance_m: float, duration_s: float,
         steps: [{text, distance_m, duration_s}, ...], extras: {lit_pct, sidewalk_pct, ...}}
    Or {ok: False, error: str, code: str} on failure.
    """
    key = _ors_key()
    if not key:
        return {'ok': False, 'code': 'no_key',
                'error': 'Routing is not configured. Set ORS_API_KEY in the server .env to enable directions.'}

    if mode not in _PROFILES:
        return {'ok': False, 'code': 'bad_mode', 'error': f'Unknown travel mode "{mode}".'}

    profile_map = _SAFE_PROFILES if prefer == 'safe' else _PROFILES
    profile = profile_map[mode]

    coords = [[origin[1], origin[0]]]
    for wp in (waypoints or []):
        coords.append([wp[1], wp[0]])
    coords.append([destination[1], destination[0]])

    # ORS POST body. `extra_info` requests way-level metadata (sidewalk, surface,
    # lit) so we can compute safety percentages without a second request.
    body = {
        'coordinates': coords,
        'instructions': True,
        'instructions_format': 'text',
        'geometry': True,
        'preference': 'shortest' if prefer == 'scenic' else 'recommended',
        'units': 'm',
    }
    if mode != 'drive':
        body['extra_info'] = ['waytype', 'surface', 'steepness']

    url = f'{ORS_BASE}/v2/directions/{profile}/geojson'
    try:
        resp = requests.post(url, json=body, timeout=20, headers={
            'Authorization': key,
            'Content-Type': 'application/json',
            'Accept': 'application/json, application/geo+json',
        })
    except requests.RequestException as e:
        return {'ok': False, 'code': 'network', 'error': f'Could not reach routing service: {e}'}

    if resp.status_code == 403:
        return {'ok': False, 'code': 'auth',
                'error': 'ORS rejected the API key. Check ORS_API_KEY in .env.'}
    if resp.status_code == 404:
        return {'ok': False, 'code': 'no_route',
                'error': 'No route found between those points for this mode.'}
    if not resp.ok:
        # ORS often returns a JSON body with .error.message; surface that if present.
        try:
            err = resp.json().get('error', {})
            msg = err.get('message') if isinstance(err, dict) else str(err)
        except ValueError:
            msg = resp.text[:200]
        return {'ok': False, 'code': f'http_{resp.status_code}',
                'error': msg or f'Routing service returned HTTP {resp.status_code}.'}

    data = resp.json()
    features = data.get('features') or []
    if not features:
        return {'ok': False, 'code': 'empty', 'error': 'Routing service returned no route.'}

    feat = features[0]
    props = feat.get('properties', {}) or {}
    summary = props.get('summary', {}) or {}
    geom = feat.get('geometry', {}).get('coordinates') or []
    # GeoJSON is [lon,lat]; flip to [lat,lng] for Leaflet.
    latlng_geom = [[c[1], c[0]] for c in geom]

    steps = []
    for segment in (props.get('segments') or []):
        for step in (segment.get('steps') or []):
            steps.append({
                'text': step.get('instruction', '').strip(),
                'distance_m': step.get('distance', 0),
                'duration_s': step.get('duration', 0),
            })

    extras_out = {}
    raw_extras = props.get('extras') or {}
    # Surface/waytype distributions help compute "% of route on dedicated path".
    if 'waytype' in raw_extras:
        extras_out['waytype_summary'] = raw_extras['waytype'].get('summary') or []
    if 'surface' in raw_extras:
        extras_out['surface_summary'] = raw_extras['surface'].get('summary') or []

    return {
        'ok': True,
        'geometry': latlng_geom,
        'distance_m': summary.get('distance', 0),
        'duration_s': summary.get('duration', 0),
        'steps': steps,
        'extras': extras_out,
        'profile': profile,
        'preference': prefer,
    }


# ── Geocoding ────────────────────────────────────────────────────────────────

def geocode(query, limit=5):
    """Resolve a free-text address to coordinates, biased toward Burgas.

    Returns: {ok, results: [{display_name, lat, lng, type, importance}, ...]}.
    Never raises; on failure returns ok=False with a friendly error.
    """
    query = (query or '').strip()
    if not query:
        return {'ok': False, 'code': 'empty', 'error': 'Empty query.'}

    params = {
        'q': query,
        'format': 'jsonv2',
        'addressdetails': 1,
        'limit': str(limit),
        # viewbox=lon_min,lat_max,lon_max,lat_min (Nominatim's idiosyncratic order)
        'viewbox': f'{BURGAS_VIEWBOX[0]},{BURGAS_VIEWBOX[3]},{BURGAS_VIEWBOX[2]},{BURGAS_VIEWBOX[1]}',
        'bounded': '0',   # bias, don't restrict — let the user search Sofia too
        'countrycodes': 'bg',
    }
    try:
        resp = requests.get(f'{NOMINATIM_BASE}/search', params=params, timeout=10,
                            headers={'User-Agent': _nominatim_user_agent(),
                                     'Accept-Language': 'bg,en'})
    except requests.RequestException as e:
        return {'ok': False, 'code': 'network', 'error': f'Geocoder unreachable: {e}'}

    if not resp.ok:
        return {'ok': False, 'code': f'http_{resp.status_code}',
                'error': f'Geocoder returned HTTP {resp.status_code}.'}

    raw = resp.json() if resp.content else []
    results = []
    for r in raw:
        try:
            results.append({
                'display_name': r.get('display_name', ''),
                'lat': float(r['lat']),
                'lng': float(r['lon']),
                'type': r.get('type', ''),
                'importance': r.get('importance', 0),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return {'ok': True, 'results': results}


def reverse_geocode(lat, lng):
    """Coordinates → human-readable label. Used when the user taps the map."""
    try:
        resp = requests.get(f'{NOMINATIM_BASE}/reverse', timeout=10,
                            params={'lat': lat, 'lon': lng, 'format': 'jsonv2',
                                    'addressdetails': 1, 'zoom': 17},
                            headers={'User-Agent': _nominatim_user_agent(),
                                     'Accept-Language': 'bg,en'})
    except requests.RequestException:
        return {'ok': False, 'error': 'unreachable'}
    if not resp.ok:
        return {'ok': False, 'error': f'http_{resp.status_code}'}
    data = resp.json() or {}
    return {'ok': True, 'display_name': data.get('display_name', f'{lat:.4f}, {lng:.4f}')}


# ── Lit-street / safety post-processing ──────────────────────────────────────

def estimate_lit_percentage(geometry, lit_ways_index=None):
    """Placeholder for a future lit-street calculation. ORS doesn't yet expose
    OSM `lit=*` directly, so we'll plug in an Overpass-API-backed index when
    we have one. For now returns None to signal 'unknown'."""
    return None


# ── Distance helpers (for nearest-point lookups) ─────────────────────────────

def haversine_m(a, b):
    """Public alias used by the /api/nearest endpoint."""
    return _haversine_m(a, b)


def closest_in_geometry(geometry, point, threshold_m=80):
    """Return the minimum distance (m) from `point` to any vertex of `geometry`,
    or None if no vertex is within `threshold_m`. Used to flag hazards near a
    proposed route without setting up a real spatial index."""
    if not geometry:
        return None
    best = None
    for v in geometry:
        d = _haversine_m(v, point)
        if best is None or d < best:
            best = d
        if best <= threshold_m * 0.5:
            break  # close enough; no need to keep scanning
    return best if best is not None and best <= threshold_m else None
