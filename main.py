import json
import os
import re
import sys
import yaml

from adapters import ADAPTERS
from notify import send_alert, send_error_alert

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"companies": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _word_in(word, text):
    """Whole-word-ish match, case-insensitive. Matches the bare word plus a
    small set of common suffixes (so 'intern' still matches 'Internship' and
    'Interns'), while still refusing to match inside unrelated words like
    'International' or 'Internals' -- those fail because 'ational'/'als'
    aren't in the allowed suffix list, so the required word boundary after
    the match never lines up."""
    pattern = r"\b" + re.escape(word.lower()) + r"(?:s|ship|ships)?\b"
    return re.search(pattern, text.lower()) is not None


def matches_filters(title, keywords, exclude):
    if keywords and not any(_word_in(k, title) for k in keywords):
        return False
    if exclude and any(_word_in(e, title) for e in exclude):
        return False
    return True


# Countries/regions to reject when us_only is set. Deliberately NOT an allowlist --
# a blank, missing, or unrecognized location is always kept. This list only needs
# to catch clear non-US signals; ambiguous names (e.g. "Georgia" the country vs.
# the US state) are left out on purpose to avoid false rejections.
NON_US_MARKERS = [
    "india", "canada", "mexico", "brazil", "argentina", "chile", "colombia", "peru",
    "united kingdom", "england", "scotland", "wales", "ireland",
    "germany", "france", "spain", "italy", "netherlands", "poland", "portugal",
    "romania", "austria", "switzerland", "sweden", "denmark", "norway", "finland",
    "belgium", "czech republic", "hungary", "greece",
    "china", "japan", "korea", "taiwan", "hong kong", "singapore", "indonesia",
    "malaysia", "thailand", "vietnam", "philippines",
    "australia", "new zealand",
    "israel", "united arab emirates", "saudi arabia", "egypt", "south africa",
    "bangalore", "hyderabad", "pune", "gurugram", "gurgaon", "noida", "chennai", "mumbai",
    "toronto", "vancouver", "montreal",
    "london", "dublin", "berlin", "munich", "paris", "madrid", "amsterdam", "warsaw",
    "tokyo", "seoul", "shanghai", "beijing", "shenzhen", "sydney", "melbourne",
]


def passes_location_filter(location, us_only):
    if not us_only:
        return True
    if not location:
        return True  # unknown location -- keep by default, don't risk losing a real US role
    loc = location.lower()
    return not any(marker in loc for marker in NON_US_MARKERS)


def main():
    config = load_config()
    state = load_state()
    companies_state = state.setdefault("companies", {})

    total_new = 0

    for company in config:
        name = company["name"]
        adapter_fn = ADAPTERS.get(company["adapter"])
        cstate = companies_state.setdefault(name, {"seen_ids": [], "broken": False})

        if adapter_fn is None:
            print(f"[error] unknown adapter '{company['adapter']}' for {name}")
            continue

        try:
            jobs = adapter_fn(company["params"])
        except Exception as e:
            print(f"[error] {name}: fetch failed: {e}")
            if not cstate["broken"]:
                send_error_alert(name, e)
                cstate["broken"] = True
            continue

        # fetch succeeded -- clear any prior "broken" flag silently
        cstate["broken"] = False

        keywords = company.get("keywords", [])
        exclude = company.get("exclude", [])
        us_only = company.get("us_only", False)

        keyword_matches = [j for j in jobs if matches_filters(j["title"], keywords, exclude)]
        matching = [j for j in keyword_matches if passes_location_filter(j.get("location", ""), us_only)]
        dropped_for_location = len(keyword_matches) - len(matching)

        print(
            f"[info] {name}: fetched {len(jobs)} total posting(s), "
            f"{len(keyword_matches)} matched keywords, "
            f"{dropped_for_location} dropped as non-US"
        )

        seen_ids = set(cstate["seen_ids"])
        new_jobs = [j for j in matching if j["id"] not in seen_ids]

        for job in new_jobs:
            print(f"[new] {name}: {job['title']} -> {job['url']}")
            send_alert(name, job)
            total_new += 1

        # keep seen_ids bounded to what's currently posted + anything seen
        # recently, so the file doesn't grow forever
        cstate["seen_ids"] = list(seen_ids.union(j["id"] for j in matching))

    save_state(state)
    print(f"[done] {total_new} new posting(s) found this run")


if __name__ == "__main__":
    main()
