import os
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def send_alert(company, job):
    if not WEBHOOK_URL:
        print(f"[warn] no DISCORD_WEBHOOK_URL set, skipping alert for {company}: {job['title']}")
        return
    content = f"🚨 **New posting: {company}**\n**{job['title']}**\n{job['url']}"
    try:
        r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[error] failed to send Discord alert for {company}: {e}")


def send_error_alert(company, error):
    if not WEBHOOK_URL:
        return
    content = f"⚠️ intern-watch: **{company}** check failed: `{error}`"
    try:
        requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
    except Exception:
        pass
