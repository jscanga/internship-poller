"""
Each adapter takes a `params` dict from config.yaml and returns a list of
normalized job dicts: {"id": str, "title": str, "url": str}

Keep these functions defensive -- ATS backends change shape occasionally,
and a KeyError here should never crash the whole run for every other
company. main.py wraps each company's fetch in try/except already, but
adapters should still fail loudly with a clear message when they do fail.
"""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; intern-watch/1.0; personal job-alert bot)"
}
TIMEOUT = 15


def fetch_greenhouse(params):
    slug = params["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "id": str(j["id"]),
            "title": j.get("title", ""),
            "url": j.get("absolute_url", ""),
        })
    return jobs


def fetch_lever(params):
    slug = params["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data:
        jobs.append({
            "id": str(j.get("id")),
            "title": j.get("text", ""),
            "url": j.get("hostedUrl", ""),
        })
    return jobs


def fetch_workday(params):
    tenant = params["tenant"]
    site = params["site"]
    wd = params.get("wd", "wd1")  # e.g. "wd1", "wd5", "wd12" -- varies per company, check DevTools
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    r = requests.post(url, headers=HEADERS, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobPostings", []):
        path = j.get("externalPath", "")
        jobs.append({
            "id": path or j.get("title", ""),
            "title": j.get("title", ""),
            "url": f"https://{tenant}.wd1.myworkdayjobs.com/{site}{path}",
        })
    return jobs


def _dig(data, dotted_path):
    """Walk a dotted path like 'operationResult.result.jobs' through nested dicts."""
    cur = data
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, [])
        else:
            return []
    return cur if isinstance(cur, list) else []


def fetch_generic_json(params):
    url = params["url"]
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    raw_jobs = _dig(data, params["jobs_path"])
    prefix = params.get("url_prefix", "")
    jobs = []
    for j in raw_jobs:
        job_id = str(j.get(params["id_field"], ""))
        title = j.get(params["title_field"], "")
        raw_url = str(j.get(params["url_field"], ""))
        full_url = raw_url if raw_url.startswith("http") else prefix + raw_url
        if job_id:
            jobs.append({"id": job_id, "title": title, "url": full_url})
    return jobs


def fetch_ashby(params):
    board = params["board"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        job_url = j.get("jobUrl", "")
        jobs.append({
            "id": job_url,        # Ashby's public API doesn't expose a bare job id, so the URL (which contains a UUID) doubles as one
            "title": j.get("title", ""),
            "url": job_url,
        })
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "ashby": fetch_ashby,
    "generic_json": fetch_generic_json,
}
