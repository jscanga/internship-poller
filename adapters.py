"""
Each adapter takes a `params` dict from config.yaml and returns a list of
normalized job dicts: {"id": str, "title": str, "url": str, "location": str}

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


def fetch_workday(params):
    """
    Workday exposes two different public hosting patterns depending on how the
    tenant is set up:
      host_style "jobs" (default):  {tenant}.{wd}.myworkdayjobs.com/{site}
      host_style "site":            {wd}.myworkdaysite.com/recruiting/{tenant}/{site}
    Both hit the same CXS API path, just under a different base. Check the real
    careers URL to know which one a company uses.

    IMPORTANT: Workday's searchText param has proven unreliable across several
    companies (it either doesn't filter server-side at all, or filters on
    something other than title). Prefer search_text: "" and let our own
    client-side keyword match do the work, unless the company's total catalog
    is too large to paginate through (in which case coverage will be partial
    regardless -- see the [warn] log line).
    """
    tenant = params["tenant"]
    site = params["site"]
    wd = params.get("wd", "wd1")  # e.g. "wd1", "wd5", "wd12" -- varies per company, check DevTools
    host_style = params.get("host_style", "jobs")
    search_text = params.get("search_text", "intern")

    if host_style == "site":
        api_base = f"https://{wd}.myworkdaysite.com"
        link_base = f"https://{wd}.myworkdaysite.com/recruiting/{tenant}/{site}"
    else:
        api_base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        link_base = f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    url = f"{api_base}/wday/cxs/{tenant}/{site}/jobs"

    max_pages = params.get("max_pages", 10)
    jobs = []
    offset = 0
    page_size = 20
    total_reported = None
    hit_cap = False
    for i in range(max_pages):
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
                "url": f"{link_base}{path}",
                "location": j.get("locationsText", ""),
            })
        if len(page_jobs) < page_size:
            break  # last page
        offset += page_size
        if i == max_pages - 1:
            hit_cap = True

    print(f"[debug] workday {tenant}/{site}: reported total={total_reported}, fetched={len(jobs)}")
    if hit_cap:
        print(
            f"[warn] workday {tenant}/{site}: hit the {max_pages}-page cap at {len(jobs)} postings "
            f"and there are likely more. Coverage is partial -- raise max_pages for this company "
            f"if it's a priority target."
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


def fetch_oracle_cloud(params):
    """
    Oracle Fusion Cloud HCM "Candidate Experience" sites (JPMorgan, BNY among
    them). The CE UI calls recruitingCEJobRequisitions unauthenticated, so we
    can too.

    Two things learned the hard way from a real captured request:
      - site_number is opaque per-company (e.g. "CX_3001") and NOT guessable
        from the URL slug shown in the browser (BNY's URL says "BNY-Careers"
        but the real site number is "CX_3001") -- always verify via DevTools.
      - location filtering uses locationId=<opaque per-company facet ID>, not
        the documented-sounding workLocationCountryCode (that param exists in
        Oracle's docs but didn't actually filter anything for BNY's tenant).
        The keyword value also gets wrapped in literal quotes in real captured
        requests (keyword="intern"), which we replicate here.
    """
    host = params["host"]                # e.g. "jpmc.fa.oraclecloud.com"
    site_number = params["site_number"]  # e.g. "CX_3001" -- get this from DevTools, not the URL slug
    keyword = params.get("keyword", "intern")
    location_id = params.get("location_id")  # opaque per-company facet ID, e.g. "300000000378743"
    max_pages = params.get("max_pages", 5)
    page_size = 100

    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    jobs = []
    offset = 0
    hit_cap = False
    for i in range(max_pages):
        finder_parts = [
            f"siteNumber={site_number}",
            f'keyword="{keyword}"',
            "sortBy=POSTING_DATES_DESC",
            f"limit={page_size}",
            f"offset={offset}",
        ]
        if location_id:
            finder_parts.insert(2, f"locationId={location_id}")
        finder = "findReqs;" + ",".join(finder_parts)

        r = requests.get(
            url,
            headers={
                **HEADERS,
                "Accept": "application/json",
                "ora-irc-cx-userid": "intern-watch-bot",
                "ora-irc-language": "en",
            },
            params={"onlyData": "true", "expand": "requisitionList", "finder": finder},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = _parse_json(r)

        page_jobs = []
        for item in data.get("items", []):
            for req in item.get("requisitionList", []):
                req_id = str(req.get("Id", ""))
                page_jobs.append({
                    "id": req_id,
                    "title": req.get("Title", ""),
                    "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{req_id}",
                    "location": req.get("PrimaryLocation", "") or "",
                })
        jobs.extend(page_jobs)
        if len(page_jobs) < page_size:
            break
        offset += page_size
        if i == max_pages - 1:
            hit_cap = True

    print(f"[debug] oracle_cloud {host}/{site_number}: fetched={len(jobs)}")
    if hit_cap:
        print(
            f"[warn] oracle_cloud {host}/{site_number}: hit the {max_pages}-page cap at "
            f"{len(jobs)} postings -- there may be more."
        )
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "workday": fetch_workday,
    "ashby": fetch_ashby,
    "generic_json": fetch_generic_json,
    "google_html": fetch_google,
    "radancy": fetch_radancy,
    "oracle_cloud": fetch_oracle_cloud,
}
