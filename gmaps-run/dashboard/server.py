#!/usr/bin/env python3
"""Lead dashboard for google-maps-scraper.

Serves the dashboard on port 8080 on every network interface, so it opens at
http://localhost:8080 and at http://<this-PC's-IP>:8080 from other devices on the network. It:
  * starts searches on the scraper's REST API (with email extraction on),
  * reads each job's CSV from the scraper's data folder and shows every field,
  * starts google-maps-scraper.exe itself (on 127.0.0.1:8081, local only) if it is not running.

Standard library only. Usage:  python server.py [--no-browser] [--no-start-scraper]
Environment overrides: DASHBOARD_HOST, DASHBOARD_PORT, GMAPS_SCRAPER_URL, GMAPS_DATA_DIR.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
RUN_DIR = HERE.parent
SCRAPER_EXE = RUN_DIR / ("google-maps-scraper.exe" if os.name == "nt" else "google-maps-scraper")
DATA_DIR = Path(os.environ.get("GMAPS_DATA_DIR", RUN_DIR / "webdata")).resolve()
# The scraper runs behind the dashboard on a local-only port; the dashboard owns 8080.
SCRAPER_URL = os.environ.get("GMAPS_SCRAPER_URL", "http://127.0.0.1:8081").rstrip("/")
SCRAPER_ADDR = urlsplit(SCRAPER_URL).netloc
HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")  # 0.0.0.0 = reachable via this PC's IP too
PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
SCRAPER_CONCURRENCY = max(2, min(4, (os.cpu_count() or 4) // 2))

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
JSON_FIELDS = {
    "open_hours", "popular_times", "reviews_per_rating", "images", "reservations",
    "order_online", "menu", "owner", "complete_address", "about", "user_reviews",
    "user_reviews_extended", "review_collection",
}
INT_FIELDS = {"review_count"}
FLOAT_FIELDS = {"review_rating", "latitude", "longitude"}
LIST_FIELDS = {"emails", "credit_cards_accepted"}  # written by the scraper as "a, b, c"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}

csv.field_size_limit(2**31 - 1)

# Whole-area search. Google Maps returns at most ~120 places per search, and "X in City" wording
# can return far fewer (e.g. 19 for "hotels in Jaipur"). So we look up the city's bounding box,
# split it into a grid of map zones and search "X" at each zone; the scraper de-duplicates places.
COVERAGE = {             # zone spacing (km), Google Maps zoom used for each zone
    "area": (6.0, 14),
    "dense": (3.0, 15),
    "very_dense": (1.5, 16),
}
MAX_ZONES = 150          # hard cap per job; larger areas get bigger zones instead
SCROLL_DEPTH = 20        # enough scrolls to reach Google's ~120-result end of list
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_UA = "LeadFinder-dashboard/1.0 (local lead research tool)"
GEOCODE_CACHE = HERE / "geocode-cache.json"
JOB_META = HERE / "job-meta.json"   # what the user typed for each job (zone jobs store URLs)
AREA_TYPES = {"country", "state", "region", "province", "state_district", "county", "district", "municipality",
              "city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood", "city_district",
              "borough", "locality", "postcode", "island"}
SPLIT_RE = re.compile(r"^(?P<what>.+?)\s+(?:in|near|around|at)\s+(?P<where>.+)$", re.I)

_cache: dict[str, tuple[int, int, list[dict]]] = {}
_cache_lock = threading.Lock()
_geo_lock = threading.Lock()
_geo_last = [0.0]
_meta_lock = threading.Lock()


# --------------------------------------------------------------------------- scraper API

def scraper_request(method: str, path: str, body: dict | None = None, timeout: float = 15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SCRAPER_URL + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw.strip() else None)


def scraper_alive() -> bool:
    try:
        status, _ = scraper_request("GET", "/api/v1/jobs", timeout=3)
        return status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start_scraper() -> str:
    """Start google-maps-scraper.exe (minimised console) if nothing answers on the API."""
    if scraper_alive():
        return "already running"
    if not SCRAPER_EXE.exists():
        setup = "Setup (run once).bat" if os.name == "nt" else "./setup.sh"
        return f"not found: {SCRAPER_EXE} - run {setup} first"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args = [str(SCRAPER_EXE), "-data-folder", str(DATA_DIR), "-addr", SCRAPER_ADDR,
            "-c", str(SCRAPER_CONCURRENCY)]
    kwargs: dict = {"cwd": str(RUN_DIR)}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: own console window, minimised
        kwargs.update(creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    else:  # macOS / Linux: run detached, log to scraper.log
        log = open(RUN_DIR / "scraper.log", "ab")  # noqa: SIM115 - handed to the child process
        kwargs.update(start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
    subprocess.Popen(args, **kwargs)  # noqa: S603 - fixed local executable
    for _ in range(60):
        if scraper_alive():
            return "started"
        time.sleep(1)
    return "started, but not answering yet"


# --------------------------------------------------------------------------- whole-area search

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def geocode(place: str) -> dict | None:
    """Bounding box of a city/area via OpenStreetMap Nominatim (1 request/second, cached on disk)."""
    key = " ".join(place.lower().split())
    with _geo_lock:
        cache = _read_json(GEOCODE_CACHE)
        if key in cache:
            return cache[key]
        wait = 1.1 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            f"{GEOCODE_URL}?{urlencode({'q': place, 'format': 'jsonv2', 'limit': 8})}",
            headers={"User-Agent": GEOCODE_UA, "Accept-Language": "en"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                found = json.loads(resp.read() or b"[]")
        finally:
            _geo_last[0] = time.time()
        # Only accept real areas (city, suburb, district, state, country...), never a shop,
        # restaurant or power station that merely has the name in it.
        # Every typed word must appear as a whole word in the match ("All" must not match "Allen County").
        typed = set(re.findall(r"\w+", place.lower()))
        areas = [d for d in found
                 if (d.get("category") in ("boundary", "place") or d.get("addresstype") in AREA_TYPES)
                 and typed <= set(re.findall(r"\w+", (d.get("display_name") or "").lower()))]
        result = None
        if areas:
            d = areas[0]
            south, north, west, east = (float(v) for v in d["boundingbox"])
            result = {"name": d.get("display_name") or place, "lat": float(d["lat"]), "lon": float(d["lon"]),
                      "south": south, "north": north, "west": west, "east": east,
                      "kind": d.get("addresstype") or d.get("type") or ""}
        cache[key] = result
        _write_json(GEOCODE_CACHE, cache)
        return result


BIG_AREA_TYPES = {"country", "state", "region", "province", "state_district", "county", "district",
                  "municipality", "city", "town"}


def find_area(where: str) -> tuple[str, dict] | None:
    """Geocode 'where'. If the exact text is not a known area, widen it without losing the city:
    'Malviya Nagar, Jaipur' -> 'Jaipur'; 'Malviya Nagar Jaipur' -> 'Jaipur'; 'Jaipur All' -> 'Jaipur'."""
    where = " ".join(where.split()).strip(" ,")
    box = geocode(where)
    if box:
        return where, box
    if "," in where:  # keep the part after the comma(s): usually the city
        parts = [p.strip() for p in where.split(",") if p.strip()]
        for i in range(1, len(parts)):
            text = ", ".join(parts[i:])
            box = geocode(text)
            if box:
                return text, box
    words = where.replace(",", " ").split()
    if len(words) > 1:  # last word is itself a city/state/country -> use it
        last = geocode(words[-1])
        if last and last.get("kind") in BIG_AREA_TYPES:
            return words[-1], last
    for n in range(len(words) - 1, 0, -1):  # drop trailing filler words
        text = " ".join(words[:n])
        box = geocode(text)
        if box:
            return text, box
    return None


def make_zones(box: dict, spacing_km: float, zoom: int) -> tuple[list[tuple[float, float]], int, float]:
    """Grid of zone centres covering the bounding box; widens zones if there would be too many."""
    lat_c = (box["south"] + box["north"]) / 2
    km_lat = 111.32
    km_lon = 111.32 * max(0.2, math.cos(math.radians(lat_c)))
    h_km = max(0.1, (box["north"] - box["south"]) * km_lat)
    w_km = max(0.1, (box["east"] - box["west"]) * km_lon)
    rows, cols = math.ceil(h_km / spacing_km), math.ceil(w_km / spacing_km)
    while rows * cols > MAX_ZONES:
        factor = math.sqrt(rows * cols / MAX_ZONES) * 1.01
        spacing_km *= factor
        zoom = max(8, round(zoom - math.log2(factor)))
        rows, cols = math.ceil(h_km / spacing_km), math.ceil(w_km / spacing_km)
    dlat = (box["north"] - box["south"]) / rows
    dlon = (box["east"] - box["west"]) / cols
    centres = [(box["south"] + (r + 0.5) * dlat, box["west"] + (c + 0.5) * dlon)
               for r in range(rows) for c in range(cols)]
    return centres, zoom, spacing_km


def zone_urls(what: str, centres: list[tuple[float, float]], zoom: int) -> list[str]:
    q = quote_plus(what.strip())
    return [f"https://www.google.com/maps/search/{q}/@{lat:.6f},{lon:.6f},{zoom}z" for lat, lon in centres]


def plan_search(queries: list[str], coverage: str, email: bool) -> tuple[list[str], list[dict], int, int]:
    """Turn typed searches into scraper keywords. Returns (keywords, areas, zones, auto_minutes)."""
    if coverage == "exact":
        return queries, [], 0, 20  # searched exactly as typed (Google decides the area)
    keywords: list[str] = []
    areas: list[dict] = []
    spacing_km, zoom = COVERAGE[coverage]
    for q in queries:
        m = SPLIT_RE.match(q)
        if not m:
            raise ValueError(f'Write each search as "what in where", e.g. "hotels in Jaipur" '
                             f'(could not read "{q}"), or choose "Exact search only".')
        what, where = m.group("what").strip(), m.group("where").strip()
        try:
            found = find_area(where)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            raise ValueError(f'Could not look up "{where}" on the map ({exc}). Check the internet '
                             f'connection, or choose "Exact search only".') from exc
        if not found:
            raise ValueError(f'Could not find "{where}" on the map. Check the spelling, '
                             f'or choose "Exact search only".')
        place, box = found
        centres, z, km = make_zones(box, spacing_km, zoom)
        keywords += zone_urls(what, centres, z)
        areas.append({"what": what, "typed": where, "where": place, "name": box["name"], "zones": len(centres),
                      "zone_km": round(km, 1), "zoom": z,
                      "box": [box["south"], box["west"], box["north"], box["east"]]})
    if len(keywords) > 2 * MAX_ZONES:
        raise ValueError(f"That is {len(keywords)} map zones in one job. Split the searches into "
                         f"separate jobs (max {2 * MAX_ZONES} zones each).")
    zones = len(keywords)
    # Upper limit only - the job stops by itself when every zone and place is done.
    auto_minutes = min(180, 20 + math.ceil(zones * (2.5 if email else 1.5)))
    return keywords, areas, zones, auto_minutes


def save_job_meta(job_id: str, meta: dict) -> None:
    with _meta_lock:
        data = _read_json(JOB_META)
        data[job_id] = meta
        _write_json(JOB_META, data)


# --------------------------------------------------------------------------- CSV parsing

def _convert(field: str, value: str):
    if field in JSON_FIELDS:
        if not value or value == "null":
            return None
        try:
            return json.loads(value)
        except ValueError:
            return value
    if field in INT_FIELDS:
        try:
            return int(float(value))
        except ValueError:
            return None
    if field in FLOAT_FIELDS:
        try:
            return float(value)
        except ValueError:
            return None
    if field in LIST_FIELDS:
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def load_places(job_id: str) -> list[dict]:
    """Parse <data>/<job>.csv. Tolerates a half-written last row while a job is running."""
    path = DATA_DIR / f"{job_id}.csv"
    try:
        st = path.stat()
    except FileNotFoundError:
        return []
    with _cache_lock:
        hit = _cache.get(job_id)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]

    text = path.read_bytes().decode("utf-8", errors="replace")
    places: list[dict] = []
    seen: set[str] = set()
    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    try:
        for row in reader:
            if header is None:
                header = row
                continue
            if len(row) != len(header):
                continue  # incomplete row still being written
            rec = {h: _convert(h, v) for h, v in zip(header, row)}
            ident = rec.get("place_id") or rec.get("cid") or rec.get("data_id") or rec.get("link")
            if ident:
                if ident in seen:
                    continue  # same place found from two overlapping zones
                seen.add(ident)
            rec["_n"] = len(places) + 1
            places.append(rec)
    except csv.Error:
        pass  # unterminated final row; keep what parsed cleanly

    with _cache_lock:
        _cache[job_id] = (st.st_mtime_ns, st.st_size, places)
    return places


def job_summary(job: dict, meta: dict) -> dict:
    data = job.get("Data") or {}
    job_id = job.get("ID", "")
    places = load_places(job_id) if UUID_RE.match(job_id) else []
    keywords = data.get("keywords") or []
    m = meta.get(job_id) or {}
    zones = m.get("zones") or sum(1 for k in keywords if k.startswith("http"))
    return {
        "id": job_id,
        "name": job.get("Name"),
        "date": job.get("Date"),
        "status": job.get("Status"),
        "keywords": m.get("queries") or [k for k in keywords if not k.startswith("http")] or keywords[:1],
        "coverage": m.get("coverage") or ("area" if zones else "exact"),
        "zones": zones,
        "areas": m.get("areas") or [],
        "email": bool(data.get("email")),
        "depth": data.get("depth"),
        "extra_reviews": bool(data.get("extra_reviews")),
        "max_minutes": int((data.get("max_time") or 0) / 60e9),
        "count": len(places),
    }


# --------------------------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    server_version = "LeadDashboard/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("DASHBOARD_VERBOSE"):
            super().log_message(fmt, *args)

    # helpers -----------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _same_origin(self) -> bool:
        # Refuse cross-site form posts: the page must come from the same host:port it posts to
        # (works for localhost, 127.0.0.1 and this PC's LAN IP alike).
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers.get('Host', '')}"

    # routes ------------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self._static(HERE / "index.html")
            if path.startswith("/vendor/"):
                target = (HERE / path.lstrip("/")).resolve()
                if HERE / "vendor" in target.parents:
                    return self._static(target)
                return self._error(404, "not found")
            if path == "/api/health":
                return self._json(200, {"scraper": scraper_alive(), "scraper_url": SCRAPER_URL,
                                        "data_dir": str(DATA_DIR)})
            if path == "/api/jobs":
                _, jobs = scraper_request("GET", "/api/v1/jobs")
                jobs = sorted(jobs or [], key=lambda j: j.get("Date") or "", reverse=True)
                meta = _read_json(JOB_META)
                return self._json(200, [job_summary(j, meta) for j in jobs])
            m = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/(places|raw\.csv)", path)
            if m and UUID_RE.match(m.group(1)):
                job_id, what = m.groups()
                if what == "places":
                    return self._json(200, load_places(job_id))
                csv_path = DATA_DIR / f"{job_id}.csv"
                if not csv_path.exists():
                    return self._error(404, "no results file yet")
                return self._send(200, csv_path.read_bytes(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{job_id}.csv"'})
            return self._error(404, "not found")
        except (urllib.error.URLError, OSError) as exc:
            return self._error(502, f"scraper not reachable at {SCRAPER_URL}: {exc}")

    def do_POST(self):
        if not self._same_origin():
            return self._error(403, "cross-origin request refused")
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/jobs":
                return self._create_job(self._body())
            if path == "/api/plan":
                body = self._body()
                queries = [q.strip() for q in str(body.get("query", "")).splitlines() if q.strip()][:20]
                coverage = str(body.get("coverage", "area"))
                if not queries or (coverage != "exact" and coverage not in COVERAGE):
                    raise ValueError("Nothing to plan.")
                _, areas, zones, minutes = plan_search(queries, coverage, bool(body.get("email", True)))
                return self._json(200, {"zones": zones, "areas": areas, "minutes": minutes})
            if path == "/api/scraper/start":
                return self._json(200, {"result": start_scraper()})
            return self._error(404, "not found")
        except ValueError as exc:
            return self._error(400, str(exc))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return self._error(502, f"scraper rejected the job: {detail}")
        except (urllib.error.URLError, OSError) as exc:
            return self._error(502, f"scraper not reachable at {SCRAPER_URL}: {exc}")

    def do_DELETE(self):
        if not self._same_origin():
            return self._error(403, "cross-origin request refused")
        m = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})", self.path.split("?", 1)[0])
        if not (m and UUID_RE.match(m.group(1))):
            return self._error(404, "not found")
        try:
            scraper_request("DELETE", f"/api/v1/jobs/{m.group(1)}")
            with _cache_lock:
                _cache.pop(m.group(1), None)
            return self._json(200, {"deleted": m.group(1)})
        except (urllib.error.URLError, OSError) as exc:
            return self._error(502, f"could not delete: {exc}")

    # actions -----------------------------------------------------------------
    def _create_job(self, body: dict):
        queries = [q.strip() for q in str(body.get("query", "")).splitlines() if q.strip()]
        if not queries:
            raise ValueError("Type what to search for, e.g. 'hotels in Jaipur'.")
        if len(queries) > 20 or any(len(q) > 200 for q in queries):
            raise ValueError("Up to 20 searches per job, each under 200 characters.")
        coverage = str(body.get("coverage", "area"))
        if coverage != "exact" and coverage not in COVERAGE:
            raise ValueError("Unknown coverage option.")
        minutes = int(body.get("max_minutes", 0))  # 0 = automatic
        lang = str(body.get("lang", "en")).lower()
        email = bool(body.get("email", True))
        if not re.fullmatch(r"[a-z]{2}", lang):
            raise ValueError("Invalid language.")

        keywords, areas, zones, auto_minutes = plan_search(queries, coverage, email)
        if minutes == 0:
            minutes = auto_minutes
        if not 3 <= minutes <= 180:
            raise ValueError("Time limit must be between 3 and 180 minutes.")

        payload = {
            "Name": str(body.get("name") or queries[0])[:120],
            "keywords": keywords,
            "lang": lang,
            # No lat/lon: zone URLs carry their own position, and exact searches let the place
            # name decide. (The scraper's own form sends 0,0, which centres the map in the ocean.)
            "lat": "", "lon": "", "zoom": 15, "radius": 10000,
            "fast_mode": False,
            "depth": SCROLL_DEPTH,
            "email": email,
            "extra_reviews": bool(body.get("extra_reviews", False)),
            "max_time": minutes * 60,  # the API takes seconds
            "proxies": [],
        }
        status, resp = scraper_request("POST", "/api/v1/jobs", payload)
        job_id = (resp or {}).get("id")
        if job_id:
            save_job_meta(job_id, {"queries": queries, "coverage": coverage, "zones": zones,
                                   "areas": areas, "minutes": minutes})
        return self._json(201 if status in (200, 201) else status,
                          {"id": job_id, "zones": zones, "areas": areas, "minutes": minutes})

    def _static(self, target: Path):
        if not target.is_file():
            return self._error(404, "not found")
        ctype = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
        extra = {}
        if target.suffix == ".html":
            extra["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
            )
        return self._send(200, target.read_bytes(), ctype, extra)


# --------------------------------------------------------------------------- main

def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def what_is_on_port() -> str:
    """Identify what already listens on our port: this dashboard, the scraper, or something else."""
    base = f"http://127.0.0.1:{PORT}"
    for path, kind in (("/api/health", "dashboard"), ("/api/v1/jobs", "scraper")):
        try:
            with urllib.request.urlopen(base + path, timeout=3) as resp:
                body = json.loads(resp.read() or b"null")
                if kind == "dashboard" and isinstance(body, dict) and "scraper" in body:
                    return "dashboard"
                if kind == "scraper" and isinstance(body, list):
                    return "scraper"
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return "other"


def main() -> int:
    no_browser = "--no-browser" in sys.argv
    local_url = f"http://localhost:{PORT}/"
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        owner = what_is_on_port()
        if owner == "dashboard":
            print(f"Lead Finder is already running - opening {local_url}")
            if not no_browser:
                webbrowser.open(local_url)
            return 0
        if owner == "scraper":
            print(f"Port {PORT} is taken by google-maps-scraper.exe started on its own.\n"
                  f"Close that scraper window (wait until no search is running), then run\n"
                  f"'Open Lead Finder.bat' again - it starts the scraper for you on {SCRAPER_ADDR}.")
        else:
            print(f"Port {PORT} is already used by another program. Close it, or set DASHBOARD_PORT.")
        return 1

    if "--no-start-scraper" not in sys.argv:
        print("Checking Google Maps scraper ...", flush=True)
        print(f"  scraper: {start_scraper()}  ({SCRAPER_URL}, data: {DATA_DIR})", flush=True)

    print(f"\nLead Finder dashboard is running. Open it at:\n  {local_url}   (this PC)", flush=True)
    if HOST in ("0.0.0.0", ""):
        for ip in lan_ips():
            print(f"  http://{ip}:{PORT}/   (other devices on your network)", flush=True)
    print("Close this window to stop the dashboard.\n", flush=True)
    url = local_url
    if not no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
