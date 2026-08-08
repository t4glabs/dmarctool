"""
Dashboard. Run with: ./venv/bin/uvicorn app.web:app --host 127.0.0.1 --port 8787

One card per domain on the overview, a full detail page per domain (DNS vs. reports,
recommendation, pass-rate trend, known senders, open action items, manual log), and
a settings page for the recommendation thresholds.

A background scheduler re-runs analysis + DNS checks every few hours while this
process is up, in addition to the explicit "run now" button and automatic re-run
after each ingest.
"""

import csv
import io
import shutil
import tempfile
from datetime import date as _date, timedelta as _timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analysis import (
    all_domains, current_policy_run, daily_pass_series, domain_window_stats,
    day_to_date, epoch_day, ensure_default_settings, postmaster_daily_series,
    provider_breakdown, run_analysis, ses_daily_series, sending_stream_breakdown,
)
from app.actions import log_action, resolve_action
from app.blocklist import run_blocklist_checks
from app.charts import dual_rate_sparkline, pass_rate_sparkline, spam_rate_sparkline
from app.compliance import run_compliance_checks
from app.db import get_connection, init_db
from app.dns_check import run_dns_checks
from app.mailgun import run_mailgun_checks
from app.postmaster import run_postmaster_checks
from app.ses_events import run_ses_event_ingest
from app.ingest import ingest_source
from app.labels import (
    SETTINGS_META, category_help, category_label, category_remediation, classification_help,
    classification_label, dns_status_help, dns_status_label, explain_policy,
    postmaster_remediation, postmaster_requirement_label,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update({
    "explain_policy": explain_policy,
    "category_label": category_label,
    "category_help": category_help,
    "classification_label": classification_label,
    "classification_help": classification_help,
    "dns_status_label": dns_status_label,
    "dns_status_help": dns_status_help,
    "postmaster_requirement_label": postmaster_requirement_label,
    "category_remediation": category_remediation,
    "postmaster_remediation": postmaster_remediation,
})

CLASSIFICATIONS = ["unclassified", "ses_newsletter", "ses_pool", "workspace", "primary_domain", "ignored"]

app = FastAPI(title="DMARCTool")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_scheduler = BackgroundScheduler()


@app.on_event("startup")
def _startup():
    conn = get_connection()
    init_db(conn)
    ensure_default_settings(conn)

    def _job():
        c = get_connection()
        run_analysis(c, verbose=False)
        run_dns_checks(c, verbose=False)
        run_blocklist_checks(c, verbose=False)
        run_compliance_checks(c, verbose=False)
        run_mailgun_checks(c, verbose=False)
        run_postmaster_checks(c, verbose=False)
        run_ses_event_ingest(c, verbose=False)

    _scheduler.add_job(_job, "interval", hours=6, id="periodic_refresh", replace_existing=True)
    _scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    _scheduler.shutdown(wait=False)


def _fmt_date(epoch_ts):
    return day_to_date(epoch_day(epoch_ts)) if epoch_ts is not None else None


def build_domain_summary(conn, domain_row, settings):
    domain_id, name = domain_row["id"], domain_row["name"]

    latest_dns = conn.execute(
        "SELECT * FROM dns_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    report_run = current_policy_run(conn, domain_id)
    latest_report = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id=?", (domain_id,)
    ).fetchone()
    last_ingested = _fmt_date(latest_report["latest"])

    window_days = int(settings["rolling_window_days"])
    window_total, window_rate = None, None
    if latest_report["latest"]:
        window_start = latest_report["latest"] - window_days * 86400
        total, passed, rate = domain_window_stats(conn, domain_id, window_start, latest_report["latest"])
        window_total = total
        window_rate = rate if total else None

    rec_row = conn.execute(
        "SELECT title FROM action_items WHERE domain_id=? AND category='ramp_recommendation' AND status='open'",
        (domain_id,),
    ).fetchone()

    open_counts = {}
    for row in conn.execute(
        """SELECT category, COUNT(*) as n FROM action_items
           WHERE domain_id=? AND status='open' AND category != 'ramp_recommendation'
           GROUP BY category""",
        (domain_id,),
    ):
        open_counts[row["category"]] = row["n"]

    return {
        "name": name,
        "dns_status": latest_dns["status"] if latest_dns else "unknown",
        "dns_match": bool(latest_dns["matches_expected"]) if latest_dns and latest_dns["matches_expected"] is not None else None,
        "dns_p": latest_dns["parsed_p"] if latest_dns else None,
        "dns_pct": latest_dns["parsed_pct"] if latest_dns else None,
        "report_p": report_run["p"] if report_run else None,
        "report_pct": report_run["pct"] if report_run else None,
        "report_since": _fmt_date(report_run["observed_from"]) if report_run else None,
        "window_days": window_days,
        "window_total": window_total,
        "window_rate": window_rate,
        "last_ingested": last_ingested,
        "open_counts": open_counts,
        "rec_title": rec_row["title"] if rec_row else None,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, flash: str = None):
    conn = get_connection()
    settings = ensure_default_settings(conn)
    domains = [build_domain_summary(conn, d, settings) for d in all_domains(conn)]
    return templates.TemplateResponse(request, "index.html", {"domains": domains, "flash": flash})


@app.get("/domain/{name}", response_class=HTMLResponse)
def domain_detail(request: Request, name: str, flash: str = None):
    conn = get_connection()
    settings = ensure_default_settings(conn)
    domain = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
    if not domain:
        return HTMLResponse(f"Unknown domain: {name}", status_code=404)
    domain_id = domain["id"]

    latest_dns = conn.execute(
        "SELECT * FROM dns_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    dns_history = conn.execute(
        "SELECT * FROM dns_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 15", (domain_id,)
    ).fetchall()

    report_run = current_policy_run(conn, domain_id)
    manual_run = conn.execute(
        """SELECT * FROM policy_history WHERE domain_id=? AND source='manual_log' AND observed_to IS NULL
           ORDER BY observed_from DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()

    latest_report = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id=?", (domain_id,)
    ).fetchone()
    window_days = int(settings["rolling_window_days"])
    window_total, window_rate = None, None
    providers = []
    streams = []
    if latest_report["latest"]:
        window_start = latest_report["latest"] - window_days * 86400
        total, passed, rate = domain_window_stats(conn, domain_id, window_start, latest_report["latest"])
        window_total, window_rate = total, (rate if total else None)
        providers = provider_breakdown(conn, domain_id, window_start, latest_report["latest"])
        streams = sending_stream_breakdown(conn, domain_id, window_start, latest_report["latest"])

    series = daily_pass_series(conn, domain_id, days=60)
    sparkline_svg = pass_rate_sparkline(series, threshold=float(settings["min_pass_rate"]))

    rec_row = conn.execute(
        "SELECT title, detail FROM action_items WHERE domain_id=? AND category='ramp_recommendation' AND status='open'",
        (domain_id,),
    ).fetchone()

    open_items = conn.execute(
        """SELECT * FROM action_items WHERE domain_id=? AND status='open' AND category != 'ramp_recommendation'
           ORDER BY created_at DESC""",
        (domain_id,),
    ).fetchall()

    senders = conn.execute(
        "SELECT * FROM known_senders WHERE domain_id=? ORDER BY total_msgs DESC LIMIT 50", (domain_id,)
    ).fetchall()
    senders = [
        dict(
            s, first_seen_date=_fmt_date(s["first_seen"]), last_seen_date=_fmt_date(s["last_seen"]),
            blocklist_status=conn.execute(
                "SELECT status, note FROM blocklist_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
                (s["source_ip"],),
            ).fetchone(),
            ptr_status=conn.execute(
                "SELECT status, ptr_hostname, note FROM ptr_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
                (s["source_ip"],),
            ).fetchone(),
        )
        for s in senders
    ]

    spf_checks = conn.execute(
        "SELECT * FROM spf_checks WHERE domain_id=? AND checked_at = (SELECT MAX(checked_at) FROM spf_checks sc2 WHERE sc2.domain_id=spf_checks.domain_id AND sc2.spf_domain=spf_checks.spf_domain) ORDER BY spf_domain",
        (domain_id,),
    ).fetchall()
    dkim_checks = conn.execute(
        "SELECT * FROM dkim_checks WHERE domain_id=? AND checked_at = (SELECT MAX(checked_at) FROM dkim_checks dc2 WHERE dc2.domain_id=dkim_checks.domain_id AND dc2.signing_domain=dkim_checks.signing_domain AND dc2.selector=dkim_checks.selector) ORDER BY signing_domain, selector",
        (domain_id,),
    ).fetchall()

    mailgun_stats = conn.execute(
        """SELECT * FROM mailgun_stats WHERE domain_id=?
           AND checked_at = (SELECT MAX(checked_at) FROM mailgun_stats ms2 WHERE ms2.mailgun_domain=mailgun_stats.mailgun_domain)
           ORDER BY mailgun_domain""",
        (domain_id,),
    ).fetchall()
    mailgun_suppression_counts = {}
    for row in conn.execute(
        """SELECT mailgun_domain, kind, COUNT(*) as n FROM mailgun_suppressions
           WHERE domain_id=? GROUP BY mailgun_domain, kind""",
        (domain_id,),
    ):
        mailgun_suppression_counts.setdefault(row["mailgun_domain"], {})[row["kind"]] = row["n"]

    postmaster_stats = conn.execute(
        """SELECT * FROM postmaster_stats WHERE domain_id=?
           AND checked_at = (SELECT MAX(checked_at) FROM postmaster_stats ps2 WHERE ps2.postmaster_domain=postmaster_stats.postmaster_domain)
           ORDER BY postmaster_domain""",
        (domain_id,),
    ).fetchall()
    postmaster_compliance = conn.execute(
        """SELECT * FROM postmaster_compliance WHERE domain_id=?
           AND checked_at = (SELECT MAX(checked_at) FROM postmaster_compliance pc2
                             WHERE pc2.postmaster_domain=postmaster_compliance.postmaster_domain
                               AND pc2.requirement=postmaster_compliance.requirement)
           ORDER BY postmaster_domain, requirement""",
        (domain_id,),
    ).fetchall()
    postmaster_spam_sparkline = spam_rate_sparkline(postmaster_daily_series(conn, domain_id, days=60))

    ses_window_days = int(settings["ses_stats_window_days"])
    ses_cutoff = (_date.today() - _timedelta(days=ses_window_days)).isoformat()
    ses_stats = conn.execute(
        """SELECT configuration_set,
                  SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
           FROM ses_event_counts WHERE domain_id=? AND day >= ?
           GROUP BY configuration_set ORDER BY configuration_set""",
        (domain_id, ses_cutoff),
    ).fetchall()
    ses_suppression_counts = {}
    for row in conn.execute(
        """SELECT configuration_set, kind, COUNT(*) as n FROM ses_suppressions
           WHERE domain_id=? GROUP BY configuration_set, kind""",
        (domain_id,),
    ):
        ses_suppression_counts.setdefault(row["configuration_set"], {})[row["kind"]] = row["n"]
    ses_rate_sparkline = dual_rate_sparkline(ses_daily_series(conn, domain_id, days=60))

    manual_log_items = conn.execute(
        "SELECT * FROM action_items WHERE domain_id=? AND kind='manual_log' ORDER BY created_at DESC LIMIT 20",
        (domain_id,),
    ).fetchall()

    return templates.TemplateResponse(request, "domain.html", {
        "flash": flash,
        "domain": domain,
        "dns_status": latest_dns["status"] if latest_dns else "unknown",
        "dns_match": bool(latest_dns["matches_expected"]) if latest_dns and latest_dns["matches_expected"] is not None else None,
        "dns_p": latest_dns["parsed_p"] if latest_dns else None,
        "dns_pct": latest_dns["parsed_pct"] if latest_dns else None,
        "dns_txt": latest_dns["txt_value"] if latest_dns else None,
        "dns_note": latest_dns["note"] if latest_dns else None,
        "report_run": {"p": report_run["p"], "pct": report_run["pct"], "observed_from": _fmt_date(report_run["observed_from"])} if report_run else None,
        "manual_run": {"p": manual_run["p"], "pct": manual_run["pct"], "observed_from": _fmt_date(manual_run["observed_from"])} if manual_run else None,
        "recommendation": {"title": rec_row["title"], "detail": rec_row["detail"]} if rec_row else None,
        "window_days": window_days, "window_total": window_total, "window_rate": window_rate,
        "sparkline_svg": sparkline_svg,
        "providers": providers,
        "streams": streams,
        "spf_checks": spf_checks,
        "dkim_checks": dkim_checks,
        "mailgun_stats": mailgun_stats,
        "mailgun_suppression_counts": mailgun_suppression_counts,
        "postmaster_stats": postmaster_stats,
        "postmaster_compliance": postmaster_compliance,
        "postmaster_spam_sparkline": postmaster_spam_sparkline,
        "ses_stats": ses_stats,
        "ses_window_days": ses_window_days,
        "ses_suppression_counts": ses_suppression_counts,
        "ses_rate_sparkline": ses_rate_sparkline,
        "open_items": open_items,
        "senders": senders,
        "classifications": CLASSIFICATIONS,
        "manual_log_items": manual_log_items,
        "dns_history": dns_history,
    })


@app.get("/domain/{name}/suppressions.csv")
def download_suppressions(name: str):
    conn = get_connection()
    domain = conn.execute("SELECT id FROM domains WHERE name=?", (name,)).fetchone()
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    domain_id = domain["id"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source", "source_domain", "email", "kind", "bounce_type", "reason", "first_seen", "last_seen"])

    for r in conn.execute(
        """SELECT mailgun_domain, email, kind, reason, first_seen_at, last_checked_at
           FROM mailgun_suppressions WHERE domain_id=? ORDER BY kind, email""",
        (domain_id,),
    ):
        writer.writerow(["mailgun", r["mailgun_domain"], r["email"], r["kind"], "",
                          r["reason"] or "", r["first_seen_at"], r["last_checked_at"]])

    for r in conn.execute(
        """SELECT configuration_set, email, kind, bounce_type, reason, first_seen_at, last_seen_at
           FROM ses_suppressions WHERE domain_id=? ORDER BY kind, email""",
        (domain_id,),
    ):
        writer.writerow(["ses", r["configuration_set"], r["email"], r["kind"], r["bounce_type"] or "",
                          r["reason"] or "", r["first_seen_at"], r["last_seen_at"]])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}_suppressions.csv"'},
    )


@app.post("/domain/{name}/senders/{ip}/classify")
def classify_sender(name: str, ip: str, classification: str = Form(...)):
    conn = get_connection()
    domain = conn.execute("SELECT id FROM domains WHERE name=?", (name,)).fetchone()
    conn.execute(
        "UPDATE known_senders SET classification=? WHERE domain_id=? AND source_ip=?",
        (classification, domain["id"], ip),
    )
    conn.commit()
    return RedirectResponse(f"/domain/{name}", status_code=303)


@app.post("/domain/{name}/log")
def log_manual_action(name: str, message: str = Form(...), p: str = Form(""), pct: str = Form(""), date: str = Form("")):
    conn = get_connection()
    log_action(
        conn, name, message,
        p=p or None,
        pct=int(pct) if pct.strip() else None,
        when=date or None,
    )
    return RedirectResponse(f"/domain/{name}", status_code=303)


@app.post("/action_items/{item_id}/resolve")
def resolve_item(item_id: int, status: str = Form("done"), redirect_to: str = Form("/")):
    conn = get_connection()
    resolve_action(conn, item_id, status)
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/ingest")
def ingest(file: UploadFile = File(...)):
    conn = get_connection()
    init_db(conn)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / file.filename
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        stats = {"attachments_seen": 0, "reports_stored": 0, "records_stored": 0, "duplicates": 0, "errors": []}
        ingest_source(conn, dest, stats)

    run_analysis(conn, verbose=False)
    try:
        run_dns_checks(conn, verbose=False)
    except Exception:
        pass

    flash = (f"Ingested {file.filename}: {stats['reports_stored']} reports stored, "
             f"{stats['duplicates']} duplicates skipped, {len(stats['errors'])} errors.")
    return RedirectResponse(f"/?flash={flash}", status_code=303)


@app.post("/run_checks")
def run_checks():
    conn = get_connection()
    run_analysis(conn, verbose=False)
    run_dns_checks(conn, verbose=False)
    run_blocklist_checks(conn, verbose=False)
    run_compliance_checks(conn, verbose=False)
    run_mailgun_checks(conn, verbose=False)
    run_postmaster_checks(conn, verbose=False)
    run_ses_event_ingest(conn, verbose=False)
    return RedirectResponse(
        "/?flash=Analysis, DNS, blocklist, compliance, Mailgun, Postmaster, and SES checks refreshed.",
        status_code=303,
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, flash: str = None):
    conn = get_connection()
    settings = ensure_default_settings(conn)
    fields = [
        {"key": key, "value": value, **SETTINGS_META.get(key, {"label": key, "help": "", "example": ""})}
        for key, value in settings.items()
    ]
    return templates.TemplateResponse(request, "settings.html", {"fields": fields, "flash": flash})


@app.post("/settings")
async def update_settings(request: Request):
    form = await request.form()
    conn = get_connection()
    for key, value in form.items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit()
    return RedirectResponse("/settings?flash=Settings saved.", status_code=303)
