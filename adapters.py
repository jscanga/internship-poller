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
            "location": (j.get("location") or {}).get("name", ""),
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
            "location": (j.get("categories") or {}).get("location", ""),
        })
    return jobs


def fetch_workday(params):
    tenant = params["tenant"]
    site = params["site"]
    wd = params.get("wd", "wd1")  # e.g. "wd1", "wd5", "wd12" -- varies per company, check DevTools
    search_text = params.get("search_text", "intern")  # server-side search avoids the 20-result page cap
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

    jobs = []
    offset = 0
    page_size = 20
    total_reported = None
    for _ in range(10):  # cap at 200 postings (10 pages)
        body = {"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": search_text}
        r = requests.post(url, headers=HEADERS, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        data = _parse_json(r)
        total_reported = data.get("total", total_reported)
        page_jobs = data.get("jobPostings", [])
        for j in page_jobs:
            path = j.get("externalPath", "")
            jobs.append({
                "id": path or j.get("title", ""),
                "title": j.get("title", ""),
                "url": f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}",
                "location": j.get("locationsText", ""),
            })
        if len(page_jobs) < page_size:
            break  # last page
        offset += page_size

    print(f"[debug] workday {tenant}/{site}: reported total={total_reported}, fetched={len(jobs)}")
    if total_reported and total_reported > len(jobs) * 3:
        print(
            f"[warn] workday {tenant}/{site}: total ({total_reported}) is much larger than what "
            f"we fetched ({len(jobs)}) -- searchText='{search_text}' likely isn't filtering "
            f"server-side, so postings beyond page {len(jobs)//page_size} could be missed"
        )
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
        location = ""
        if "location_field" in params:
            location = str(j.get(params["location_field"], ""))
        if job_id:
            jobs.append({"id": job_id, "title": title, "url": full_url, "location": location})
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
            "location": j.get("location", ""),
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
            # deliberately left blank: the URL's loc= param is a 2-letter code that's
            # ambiguous (e.g. "CA" could mean California or Canada) -- guessing here
            # risks silently dropping real US postings, so we leave it unknown and let
            # the blocklist filter keep it by default instead.
            "location": "",
        })
    return jobs


def fetch_radancy(params):
    """
    Some companies (Capital One among them) run their public career search
    through Radancy, which returns JSON with an embedded HTML fragment under
    "results" rather than clean structured job objects. We regex the job
    cards out of that fragment instead of a full HTML parser, since the
    markup is simple and consistent (one <li> per job, with a data-job-id,
    an <h2> title, and a job-location span).
    """
    import html as html_lib

    url = params["url"]
    base_url = params.get("base_url", "")
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = _parse_json(r)
    results_html = data.get("results", "")

    pattern = re.compile(
        r'<a href="([^"]+)"[^>]*data-job-id="([^"]+)">.*?<h2>([^<]+)</h2>.*?'
        r'<span class="job-location">([^<]+)</span>',
        re.DOTALL,
    )
    jobs = []
    for href, job_id, title, location in pattern.findall(results_html):
        jobs.append({
            "id": job_id,
            "title": html_lib.unescape(title),
            "url": base_url + href if href.startswith("/") else href,
            "location": html_lib.unescape(location),
        })
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "ashby": fetch_ashby,
    "generic_json": fetch_generic_json,
    "google_html": fetch_google,
    "radancy": fetch_radancy,
}
