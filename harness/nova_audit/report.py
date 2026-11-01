"""Turn the auditors' items into report.json: apply a person's decisions, mark what feedback changed (learned_at),
choose each action by mode and the unattended-action test, score confidence, and keep ids stable across reports
(supersedes / retro)."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import feedback as FB
from . import store
from .engine import Auditor, code_of, key_of, to_usd
from .model import World
from .util import ZERO, money, month_key, norm_invoice, parse_date, squash

THRESHOLD = Decimal("5000.00")
OUTBOUND = {"overcharge", "duplicate", "waived_but_billed", "late_fee_not_owed"}
SAFE_PICKUP = {"tracking", "email", "n/a"}


def state_of(it):
    return (it["verdict"], money(it["amount"]) if it.get("amount") is not None else None,
            tuple(money(x) for x in it["range"]) if it.get("range") else None)


def build(as_of_s: str, mode: str, state: str) -> dict:
    as_of = parse_date(as_of_s)
    recs = store.load_all(state)
    w = World(recs, as_of, state)
    fb = FB.load(state, as_of)
    items, _ = Auditor(w, fb).run()
    base_items, _ = Auditor(World(recs, as_of, state), {}).run() if fb["all_ids"] else (items, None)
    items = apply_key_feedback(items, fb, w)
    base_idx = {key_of(i): i for i in base_items}
    for it in items:
        k = key_of(it)
        b = base_idx.get(k)
        changed = b is None or state_of(b) != state_of(it) or it.get("fb_forced")
        ids = [i for i in it.get("fb_used", set()) if i in fb["all_ids"]] + list(fb["by_key"].get(k, []))
        if it.get("fb_forced"):
            ids = [it["fb_forced"]] + ids
        it["learned_at"] = ({"feedback_item_id": ids[0], "batch": fb["batch"].get(ids[0], "")}
                            if changed and ids else None)
    gate(items, w, mode)
    for it in items:
        it["confidence"] = confidence(it)
    hist_assign(items, state, mode, as_of, w)
    return render(items, as_of_s, mode)


def apply_key_feedback(items, fb, w: World):
    out = []
    for it in items:
        k = key_of(it)
        if k in fb["suppress"]:
            continue
        if k in fb["override"] and it["verdict"] in ("finding", "conflict", "cannot_determine"):
            amt, iid = fb["override"][k]
            it.update(verdict="finding", amount=amt, range=None, missing=None, fb_forced=iid)
        out.append(it)
    have = {key_of(i) for i in out}
    for k, o in fb["oneoff"].items():
        if k[0] != "waived_but_billed":
            continue
        ent = w.invoices.get(k[1])
        if not ent:
            continue
        inv = ent["inv"]
        c = w.contract(inv.get("contract_id"))
        codes = set(o.get("codes") or [])
        if not codes:
            for f in w.emails:
                rec = w.rec_by_doc[f["doc"]]
                for wv in f["waivers"]:
                    who = wv.get("author") if wv["quoted"] else rec["from_name"]
                    if squash(who or "") == squash(o.get("person") or ""):
                        codes.update(wv["codes"])
        months = set(o.get("months") or [])
        amt, ev = ZERO, []
        for ln in inv["lines"]:
            if ln.get("tax") or ln.get("amount") is None:
                continue
            m = ln.get("month_ref") or (month_key(ln["date"]) if ln.get("date") else None)
            code = code_of(ln["desc"], c)
            if m in months and (not codes or code in codes):
                amt += ln["amount"]
                ev.append(ln["ev"])
        if amt <= 0:
            continue
        usd, fx_ev, missing = to_usd(amt, inv.get("currency") or "USD", c, inv.get("date"))
        if missing:
            continue
        it = {"class": "waived_but_billed", "invoice_id": inv["id"], "contract_id": inv.get("contract_id"),
              "verdict": "finding", "amount": usd, "evidence": ev + ([fx_ev] if fx_ev else []),
              "pickup_src": {"n/a"}, "authority": 2, "fb_used": {o["id"]}, "decisive": set(), "lanes": set(),
              "inv_received": inv["received"], "reconciles": inv.get("reconciles"), "fb_forced": o["id"],
              "variant": "WB-oneoff"}
        out = [x for x in out if key_of(x) != k]
        out.append(it)
        have.add(k)
    return out


def gate(items, w: World, mode: str):
    per_invoice: dict = {}
    for it in items:
        if it["class"] in OUTBOUND and it["verdict"] == "finding":
            k = norm_invoice(it["invoice_id"])
            per_invoice[k] = per_invoice.get(k, ZERO) + (it["amount"] or ZERO)
    conflicts = {(c, l, p) for it in items if it["verdict"] == "conflict" for (c, l, p) in it.get("lanes", set())}
    for it in items:
        cls, v = it["class"], it["verdict"]
        c = w.contract(it.get("contract_id"))
        it["draft"] = None
        if v == "conflict":
            it["action"] = "escalate"
            continue
        if cls == "expiry":
            it["action"] = "internal:alert"
            continue
        if cls == "late_fee_risk":
            it["action"] = "internal:payment_reminder"
            continue
        if v == "cannot_determine":
            verb = "request_document"
            to = (it.get("missing") or {}).get("email")
            safe = bool(to)
            amount_key = ZERO
        elif cls == "cashback":
            if it.get("due") and w.as_of <= it["due"]:
                it["action"] = "internal:track_credit"
                continue
            verb = "claim_credit"
            p = w.contact(c, "billing_disputes") or w.contact(c, "notices")
            to = (p or {}).get("email")
            amount_key = it["amount"]
            safe = bool(to) and it.get("ok", True)
        else:
            verb = "dispute"
            p = w.contact(c, "billing_disputes")
            to = (p or {}).get("email")
            amount_key = per_invoice.get(norm_invoice(it["invoice_id"]), it["amount"] or ZERO)
            safe = bool(to) and it.get("reconciles", True) is not False
        if amount_key >= THRESHOLD:
            it["action"] = "human_review"
        elif mode != "autonomous":
            it["action"] = f"draft:{verb}"
        else:
            complete = it.get("pickup_src", {"n/a"}) <= SAFE_PICKUP
            no_conf = not (set(it.get("lanes", set())) & conflicts)
            authority = it.get("authority", 1) <= 2
            it["action"] = f"execute:{verb}" if (safe and complete and no_conf and authority) else "human_review"
        if to:
            it["draft"] = {"to": to, "subject": _subject(it, verb)}


def _subject(it, verb):
    what = it.get("invoice_id") or f"{it.get('contract_id')} {it.get('period', '')}".strip()
    return {"dispute": f"Billing dispute: {what} ({it['class'].replace('_', ' ')})",
            "claim_credit": f"Credit due: {what} ({it.get('credit_type')})",
            "request_document": f"Request for {(it.get('missing') or {}).get('name')} ({what})"}[verb]


def confidence(it) -> float:
    v, cls = it["verdict"], it["class"]
    c = {"finding": 0.93, "conflict": 0.85, "cannot_determine": 0.82}.get(v, 0.8)
    if cls == "expiry":
        c = 0.96
    if cls == "late_fee_risk":
        c = 0.92
    if cls == "cashback":
        c = 0.9 if it.get("ok", True) else 0.6
    if it.get("pickup_src") and not it["pickup_src"] <= SAFE_PICKUP:
        c -= 0.12
    if it.get("ocr"):
        c -= 0.08
    if it.get("reconciles") is False:
        c -= 0.25
    if it.get("authority", 1) >= 3:
        c -= 0.15
    elif it.get("authority", 1) >= 2:
        c -= 0.02
    if it.get("learned_at"):
        c -= 0.02
    if str(it.get("variant", "")).startswith("DU-line"):
        c -= 0.08
    return round(max(0.05, min(0.98, c)), 2)


def hist_assign(items, state, mode, as_of: date, w: World):
    hp = Path(state) / f"history_{mode}.json"
    hist = json.loads(hp.read_text()) if hp.is_file() else {}
    prev_dates = sorted(d for d in hist if d < as_of.isoformat())
    prev = hist[prev_dates[-1]] if prev_dates else None
    cur = {}
    for it in items:
        k = key_of(it)
        ks = "|".join(k)
        base = f"{k[0]}:{'/'.join(k[1:])}"
        st = list(state_of(it))
        it["supersedes"], it["retro"] = [], None
        if prev and ks in prev:
            p = prev[ks]
            if p["state"] != st:
                it["id"] = f"{base}@{as_of.isoformat()}"
                it["supersedes"] = [p["id"]]
            else:
                it["id"] = p["id"]
        else:
            it["id"] = base
            if prev is not None and it.get("inv_received", "9999") <= prev_dates[-1] + "T23:59:59Z":
                for d in it.get("decisive", set()):
                    part = w.parts_by_doc.get(d)
                    rec = part["rec"] if part else w.rec_by_doc.get(d)
                    if not rec or rec["received"][:10] <= prev_dates[-1]:
                        continue
                    eff = (part or {}).get("effective")
                    if eff and eff.isoformat() < rec["received"][:10]:
                        it["retro"] = True
        cur[ks] = {"id": it["id"], "state": st}
    hist[as_of.isoformat()] = cur
    hp.write_text(json.dumps(hist, indent=1))


def render(items, as_of_s, mode) -> dict:
    rep = {"schema_version": "1.0", "as_of": as_of_s, "mode": mode, "findings": [],
           "portfolio": {"cashback": [], "expiry": [], "late_fee_risk": []}}
    for it in sorted(items, key=lambda i: "|".join(key_of(i))):
        out = {"id": it["id"], "class": it["class"], "variant": it.get("variant")}
        if it["class"] == "cashback":
            out.update(contract_id=it["contract_id"], period=it["period"], credit_type=it["credit_type"])
        elif it["class"] == "expiry":
            out["contract_id"] = it["contract_id"]
        else:
            out["invoice_id"] = it["invoice_id"]
            if it.get("contract_id"):
                out["contract_id"] = it["contract_id"]
        out.update({
            "verdict": it["verdict"],
            "amount_usd": money(it["amount"]) if it.get("amount") is not None and it["verdict"] == "finding" else None,
            "amount_range": [money(x) for x in it["range"]] if it.get("range") else None,
            "deadline": (it.get("deadline").isoformat() if it.get("deadline") else None),
            "action": it["action"], "confidence": it["confidence"], "evidence": it["evidence"],
            "missing_doc": ({"name": it["missing"]["name"], "holder": it["missing"]["holder"]}
                            if it.get("missing") else None),
            "supersedes": it.get("supersedes") or [], "learned_at": it.get("learned_at")})
        if it.get("retro"):
            out["retro"] = True
        if it.get("draft"):
            out["draft"] = it["draft"]
        if it["class"] == "cashback":
            rep["portfolio"]["cashback"].append(out)
        elif it["class"] == "expiry":
            rep["portfolio"]["expiry"].append(out)
        elif it["class"] == "late_fee_risk":
            rep["portfolio"]["late_fee_risk"].append(out)
        else:
            rep["findings"].append(out)
    return rep
