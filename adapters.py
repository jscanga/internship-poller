"""
Each adapter takes a `params` dict from config.yaml and returns a list of
normalized job dicts: {"id": str, "title": str, "url": str}

Keep these functions defensive -- ATS backends change shape occasionally,
and a KeyError here should never crash the whole run for every other
company. main.py wraps each company's fetch in try/except already, but
adapters should still fail loudly with a clear message when they do fail.
"""
import re

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; intern-watch/1.0; personal job-alert bot)"
}
TIMEOUT = 15


def _parse_json(r):
    try:
        return r.json()
    except ValueError as e:
        snippet = r.text[:200].replace("\n", " ")
        raise RuntimeError(
            f"non-JSON response (status {r.status_code}): {snippet!r}"
        ) from e


def fetch_greenhouse(params):
    slug = params["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = _parse_json(r)
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
    data = _parse_json(r)
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
    search_text = params.get("search_text", "intern")  # server-side search avoids the 20-result page cap
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": search_text}
    r = requests.post(url, headers=HEADERS, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    data = _parse_json(r)
    jobs = []
    for j in data.get("jobPostings", []):
        path = j.get("externalPath", "")
        jobs.append({
            "id": path or j.get("title", ""),
            "title": j.get("title", ""),
            "url": f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}",
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
    data = _parse_json(r)
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
    data = _parse_json(r)
    jobs = []
    for j in data.get("jobs", []):
        job_url = j.get("jobUrl", "")
        jobs.append({
            "id": job_url,        # Ashby's public API doesn't expose a bare job id, so the URL (which contains a UUID) doubles as one
            "title": j.get("title", ""),
            "url": job_url,
        })
    return jobs


def fetch_google(params):
    """
    Google's careers site doesn't expose a clean JSON API -- job data is
    embedded directly in the search results page's HTML as part of a large
    internal data blob. This pulls out (job_id, title, apply_url) triples
    via regex instead of parsing that whole structure.

    Fragile by nature: if Google changes their page's internal format, this
    regex will start returning zero jobs (not crash) -- worth spot-checking
    every so often with the [info] log line main.py prints each run.
    """
    url = params["url"]
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text

    # matches: "123456789","Job Title Here","https://www.google.com/about/careers/applications/signin?jobId=...."
    pattern = re.compile(
        r'"(\d{10,})","([^"]+)","(https://www\.google\.com/about/careers/applications/signin\?jobId[^"]+)"'
    )
    jobs = []
    seen = set()
    for job_id, title, apply_url in pattern.findall(html):
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append({
            "id": job_id,
            "title": title.encode("utf-8").decode("unicode_escape") if "\\u" in title else title,
            "url": apply_url.replace("\\u003d", "=").replace("\\u0026", "&"),
        })
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "ashby": fetch_ashby,
    "generic_json": fetch_generic_json,
    "google_html": fetch_google,
}
