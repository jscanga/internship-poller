# intern-watch

Polls ~30 companies' career APIs every 10 minutes and pings a Discord webhook
the moment a new posting matching your keywords shows up. No page-scraping
required for most companies — Greenhouse, Lever, and Workday all expose
clean public JSON search APIs; you're just calling them the same way their
own career page's frontend does.

## 1. Get a Discord webhook

Server settings → Integrations → Webhooks → New Webhook → copy the URL.
(Make yourself a private server/channel if you don't already have one —
takes 30 seconds.)

## 2. Push this to a GitHub repo

```
cd intern-watch
git init
git add .
git commit -m "init"
gh repo create intern-watch --private --source=. --push
# or create the repo on github.com and `git remote add origin ...`
```

## 3. Add the webhook as a repo secret

Repo → Settings → Secrets and variables → Actions → New repository secret
Name: `DISCORD_WEBHOOK_URL`, value: the URL from step 1.

## 4. Turn it on

The workflow runs automatically every 10 minutes once it's on GitHub's
default branch. You can also trigger it manually from the Actions tab
("Run workflow") to test immediately instead of waiting.

## 5. VERIFY your company slugs before trusting the alerts

I pre-filled `config.yaml` with my best guess for each ATS, but a wrong
slug means that company silently returns zero results forever — worse
than not monitoring it at all, because you *think* you're covered.

**For Greenhouse / Lever companies:** open the company's real careers page,
find the "View all jobs" or similar link — it usually redirects to
`boards.greenhouse.io/<slug>` or `jobs.lever.co/<slug>`. That's your slug.
You can sanity-check it by pasting this in a browser:
`https://boards-api.greenhouse.io/v1/boards/<slug>/jobs` — if you get back
JSON with a `jobs` array, it's correct.

**For Workday companies:** open the careers page, open browser DevTools →
Network tab, filter to Fetch/XHR, then search or reload. You'll see a
request to `https://<tenant>.wd#.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`
(note: the subdomain number, wd1/wd3/wd5, varies by company — copy it
exactly from the real request). `<tenant>` and `<site>` in the URL are what
go in config.yaml.

**For Amazon / Microsoft / Google / IBM ("Big Tech"):** these run
custom search backends, not a standard ATS. Do the same DevTools trick —
open the careers search page, open Network tab, type your search term,
and find the XHR request that returns JSON. Copy that exact request URL
into `params.url`, then look at the JSON shape to fix `jobs_path`,
`id_field`, `title_field`, and `url_field` in config.yaml to match. This
is the one part of setup that takes real inspection — the API shapes do
shift over time, so don't be surprised if you have to redo this every
several months.

## 6. Add your remaining ~20 companies

Copy one of the existing blocks in `config.yaml`, figure out its adapter
type using the DevTools method above, and add it. No code changes needed
unless a company runs something outside Greenhouse/Lever/Workday/generic
JSON — if you hit one of those, tell me the URL pattern and I'll help you
write an adapter for it.

## Things worth knowing

- **GitHub's cron isn't exact.** Scheduled workflows can slip by a few
  minutes during high load (especially right at the top of the hour,
  since everyone's cron fires then). Real-world cadence is closer to
  "within ~10-15 min," not "exactly every 600 seconds." If being first
  matters enough, you could stagger to `*/7` instead of `*/10` to dodge
  the top-of-hour pileup slightly, at the cost of more runs.
- **GitHub disables scheduled workflows after 60 days of repo inactivity.**
  If you go quiet on the repo for two months, the cron silently stops.
  Worth a calendar reminder, or just push a trivial commit occasionally.
- **Be a good citizen.** 10-minute polling of official public JSON APIs is
  fine — it's the same load a human refreshing the page would generate,
  many times lighter than actual scraping. Don't drop the interval much
  below this, and don't add companies whose only option is aggressive HTML
  scraping of a page that clearly doesn't want it (that's a fast way to get
  your IP range blocked, and GitHub Actions runners share IP ranges with a
  lot of other traffic).
- **state.json gets committed back by the bot** every run so state
  persists across GitHub Actions' stateless containers. If you ever want
  to reset and re-alert on everything currently posted, just delete
  `state.json`'s contents back to `{"companies": {}}`.
