# DMARCTool — Usage Manual

A personal dashboard for keeping track of email authentication (DMARC) across your domains, so you don't have to remember it all yourself.

Open it any time at: **http://127.0.0.1:8787** (bookmark it on your Air — it runs automatically in the background, you never need to "start" it).

---

## 1. The basics, in plain language

- **DMARC** is a setting on your domain that tells other mail servers what to do with email that claims to be from you but fails authentication (could be spam, could be someone spoofing your domain).
- **p=none** — just watching. Nothing gets blocked or sent to spam because of this rule.
- **p=quarantine** — mail that fails checks gets sent to spam.
- **p=reject** — mail that fails checks gets blocked outright.
- **pct=** — what percentage of your mail this rule applies to. The safe way to tighten a domain is to raise this slowly (e.g. 10% → 25% → 50% → 100%) while watching your pass rate stay healthy, rather than jumping straight to blocking everything.
- **Pass rate** — the percentage of your mail that's authenticating correctly. This is the main number the tool watches to decide whether it's safe to move to the next step.

You don't need to know more than this to use the tool day to day — every page also has its own short explanations and hover tooltips.

---

## 2. The overview page

One card per domain. Each shows:

| Field | What it means |
|---|---|
| **Live DNS** | What's actually published in DNS right now (checked automatically every 6 hours, or on demand). |
| **Reports say** | What mail providers (Google, Yahoo, Microsoft, etc.) have told you they've seen. This can lag a few days behind a real DNS change — a mismatch here isn't necessarily a problem. |
| **Pass rate** | Your recent authentication success rate. Green = healthy, yellow = watch it, red = investigate. |
| **Last ingested** | The most recent report data on file. If this is old, add new reports (see below). |
| **Needs attention** | Open items worth a look — click into the domain to see details. |
| **Recommendation box** | The tool's plain-language suggestion for what to do next, and why. |

Click any domain name to see its full detail page.

---

## 3. Adding new DMARC reports (ingestion)

Right now this is a manual step (a fully automatic version can be added later if useful).

1. Export your DMARC reports mailbox via **Google Takeout** (Mail → the label/folder your reports go to).
2. Download the resulting `.zip`.
3. On the DMARCTool overview page, use the **"Add new reports"** file picker, choose that `.zip`, and click **Ingest**.

The tool also accepts a raw `.mbox` file, or a single `.xml`, `.xml.gz`, or `.zip` report, if you ever have one of those directly instead of a full Takeout export.

After ingesting, it automatically re-runs the analysis and a DNS check, so the dashboard reflects the new data immediately — you'll see a confirmation message like "Ingested ...: 42 reports stored, 0 duplicates skipped, 0 errors." Re-ingesting the same export twice is safe — duplicates are skipped automatically.

---

## 4. The domain detail page

- **DNS vs. reports** — side-by-side comparison, with a note if they disagree and a plain-language explanation of what the current setting actually does.
- **Recommendation** — the suggested next step, with the reasoning spelled out (e.g. "sustained 99.7% pass for 43 days — safe to raise").
- **Pass rate trend** — a simple chart of your daily pass rate over the last 60 days, with a dashed line marking the threshold you need to clear.
- **Open action items** — things worth attention (a new/unrecognized sender, a failing sender, a DNS mismatch, stale data). Each has **Mark done** (you handled it) or **Dismiss** (not relevant) — either way it won't be recreated unless the underlying issue is still true next time the tool checks.
- **Known senders** — every mail source ever seen for this domain, with a pass rate and a dropdown to label what it is once you recognize it (your bulk-mail system, Google Workspace, etc.). Unrecognized senders with a lot of failing mail are also flagged automatically as open action items.
- **Log a manual action** — record something you actually did (e.g. "raised pct 10 → 25"), optionally with the new enforcement level/percentage. This becomes the tool's ground truth for what you changed and when, independent of what reports say — useful because reports can lag by days after a real change.
- **DNS check history** — a log of every automated DNS check, so you can see exactly when your live setting last changed.

---

## 5. Settings (tuning what "safe" means)

Go to **Settings** in the top nav. Every field there has its own plain-language description and an example value — you don't need to memorize anything, just read the explanation under each field before changing it. The defaults are sensible starting points (99% pass rate required, 14 days of stability before ramping further, ladder of 10% → 25% → 50% → 100%).

---

## 6. Checking the service is alive / restarting after a manual update

The dashboard runs as a background service (`com.aikyam.dmarctool`) that starts automatically when you log in and restarts itself if it ever crashes. You normally never need to touch this. If you ever want to check on it manually (open Terminal):

```bash
# Is it running?
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/
# 200 = healthy

# Restart it (e.g. after a code change)
launchctl kickstart -k gui/$(id -u)/com.aikyam.dmarctool

# View recent logs
tail -n 50 ~/dmarctool/logs/dmarctool.out.log
tail -n 50 ~/dmarctool/logs/dmarctool.err.log
```

---

## 7. Troubleshooting

- **Dashboard won't load in the browser** — run the "Is it running?" check above. If it's not 200, check `dmarctool.err.log` for the error, then `launchctl kickstart -k gui/$(id -u)/com.aikyam.dmarctool` to restart it.
- **A domain shows "not checked yet" for Live DNS** — click **Refresh now** on the overview page, or wait for the automatic 6-hourly check.
- **A domain shows "no reports yet"** — you haven't ingested any DMARC reports for it. Add a Takeout export that includes reports for that domain.
- **Ingest says errors > 0** — usually a malformed or unsupported attachment in the export; the rest of the reports still get processed normally. This is safe to ignore unless it happens for every file.
- **You changed DNS but the dashboard still shows the old value** — this is expected for a few days; reporters batch and deliver aggregate reports on their own schedule, sometimes lagging a week. Check "Live DNS" (not "Reports say") for the real-time state, and log the change yourself under **Log a manual action** so the tool has ground truth in the meantime.

---

## 8. For reference: the command line

Everything the dashboard does is also available as scripts, if you ever want to run something directly (e.g. from a script or cron job):

```bash
cd ~/dmarctool
./venv/bin/python -m app.ingest <path-to-export>       # ingest a report export
./venv/bin/python -m app.analysis                        # recompute recommendations
./venv/bin/python -m app.dns_check                       # check live DNS for all domains
./venv/bin/python -m app.actions log <domain> "<message>" [--p p] [--pct N]   # log a manual change
./venv/bin/python -m app.actions list [domain]           # list open action items
./venv/bin/python -m app.actions resolve <item_id> [--status done|dismissed]
```
