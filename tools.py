from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(APP_DIR, "index.html")


def _origin() -> str:
    origin = os.environ.get("FUSED_RENDER_ORIGIN")
    if not origin:
        home = os.environ.get("FUSED_RENDER_HOME_DIR") or os.path.expanduser("~/.fused-render")
        try:
            with open(os.path.join(home, "server.json"), encoding="utf-8") as fh:
                origin = json.load(fh).get("origin")
        except (OSError, ValueError):
            origin = None
    return (origin or "http://127.0.0.1:1777").rstrip("/")


def _call(path: str, body: dict | None = None, method: str = "POST", timeout: float = 30) -> dict:
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(_origin() + path, data=data, method=method,
                                 headers={"X-Fused": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        raise RuntimeError(payload.get("error") or payload.get("detail") or f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"FusedRender is not running ({exc.reason}); open it and try again") from None


def _engine() -> dict:
    q = urllib.parse.urlencode({"html": PAGE})
    return _call(f"/api/apps/background/status?{q}", method="GET", timeout=10)


def _daemon(route: str, body: dict | None = None, start: bool = True, timeout: float = 30) -> dict | None:
    eng = _engine()
    if not eng.get("running"):
        if not start:
            return None
        _call("/api/apps/background/start", {"html": PAGE}, timeout=60)
        eng = _engine()
        if not eng.get("running"):
            raise RuntimeError("Open Awake's background daemon did not start")
    res = _call(f"/api/engines/{eng['engine_id']}/proxy/{route}", body, timeout=timeout)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(res["error"])
    return res


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def _summary(s: dict | None) -> dict:
    s = s or {"active": False, "log": []}
    now = s.get("now") or time.time()
    log = s.get("log") or []
    today = datetime.fromtimestamp(now).date()
    today_secs = sum(max(0, e["end"] - e["start"]) for e in log
                     if datetime.fromtimestamp(e["start"]).date() == today)
    out = {"active": bool(s.get("active"))}
    if out["active"]:
        start, ends = s.get("start"), s.get("ends")
        today_secs += max(0, now - start)
        out.update(
            started_at=_iso(start),
            ends_at=_iso(ends),
            until_stopped=not ends,
            remaining_minutes=round(max(0, ends - now) / 60, 1) if ends else None,
            keep_screen_on=bool(s.get("display")),
            lid_closed=bool(s.get("lid")),
        )
    out["awake_today_minutes"] = round(today_secs / 60, 1)
    out["recent_sessions"] = [
        {
            "started_at": _iso(e["start"]),
            "ended_at": _iso(e["end"]),
            "minutes": round((e["end"] - e["start"]) / 60, 1),
            "planned_minutes": round(e["planned"] / 60, 1) if e.get("planned") else None,
            "keep_screen_on": bool(e.get("display")),
            "lid_closed": bool(e.get("lid")),
            "reason": e.get("reason"),
        }
        for e in log[:5]
    ]
    return out


def status() -> dict:
    return _summary(_daemon("state", start=False))


def stay_awake(minutes: float = 0, keep_screen_on: bool = True, lid_closed: bool = False) -> dict:
    if minutes < 0:
        raise ValueError("minutes must be 0 (until stopped) or more")
    body = {"minutes": minutes, "display": keep_screen_on, "lid": lid_closed}
    return _summary(_daemon("start", body, timeout=180 if lid_closed else 30))


def stop() -> dict:
    return _summary(_daemon("stop", start=False))
