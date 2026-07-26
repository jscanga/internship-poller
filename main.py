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
    """Whole-word match, case-insensitive. Prevents 'intern' from matching
    inside 'International' or 'Internals'."""
    return re.search(r"\b" + re.escape(word.lower()) + r"\b", text.lower()) is not None


def matches_filters(title, keywords, exclude):
    if keywords and not any(_word_in(k, title) for k in keywords):
        return False
    if exclude and any(_word_in(e, title) for e in exclude):
        return False
    return True


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
        matching = [j for j in jobs if matches_filters(j["title"], keywords, exclude)]

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
