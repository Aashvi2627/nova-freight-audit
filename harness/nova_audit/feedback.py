"""A person's decisions (feedback.json), stored with their source and scope, and turned into the rules a report uses.

Standing facts apply only where every filter in `applies_to` matches and only inside valid_from..valid_to; one-off
decisions change exactly the key they name. A fact counts from `decided_at` on."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .util import D, norm_invoice, parse_date


def fkey(k: dict) -> tuple:
    cls = k.get("class") or ("cashback" if "period" in k else "expiry")
    if cls == "cashback":
        return ("cashback", str(k.get("contract_id")), str(k.get("period")), str(k.get("credit_type")))
    if cls == "expiry":
        return ("expiry", str(k.get("contract_id")))
    return (cls, norm_invoice(k.get("invoice_id")))


def store(path: str, state: str) -> int:
    data = json.loads(Path(path).read_text())
    d = Path(state) / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("*.json")))
    name = f"{n:03d}_{Path(path).name}"
    if not any(json.loads(f.read_text()).get("_source") == str(Path(path).name) and
               json.loads(f.read_text()).get("decided_at") == data.get("decided_at") for f in d.glob("*.json")):
        data["_source"] = Path(path).name
        (d / name).write_text(json.dumps(data, indent=1))
    return len(data.get("items") or [])


def load(state: str, as_of: date) -> dict:
    fb: dict = {"suppress": {}, "override": {}, "oneoff": {}, "authority": [], "accepted": [], "precedence": [],
                "confirm": {}, "by_key": {}, "batch": {}, "all_ids": set()}
    d = Path(state) / "feedback"
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        data = json.loads(f.read_text())
        decided = parse_date(str(data.get("decided_at") or "")[:10])
        if decided and decided > as_of:
            continue
        batch = data.get("after_batch") or ""
        for it in data.get("items") or []:
            iid = str(it.get("item_id"))
            fb["batch"][iid] = batch
            fb["all_ids"].add(iid)
            k = fkey(it.get("finding_key") or {}) if it.get("finding_key") else None
            if k:
                fb["by_key"].setdefault(k, []).append(iid)
            dec = (it.get("decision") or "").lower()
            fact = it.get("fact") or {}
            ftype = (fact.get("type") or "").lower()
            subj, val = fact.get("subject") or {}, fact.get("value") or {}
            scope = it.get("scope") or {}
            ap = scope.get("applies_to") or {}
            vf, vt = parse_date(scope.get("valid_from") or ""), parse_date(scope.get("valid_to") or "")
            if dec in ("close", "reject") and k:
                fb["suppress"][k] = iid
            if dec == "override_amount" and k:
                amt = D(val.get("amount_usd"))
                if amt is not None:
                    fb["override"][k] = (amt, iid)
            if dec == "one_off_approval" and k:
                fb["oneoff"][k] = {"person": subj.get("person"), "months": val.get("months") or [], "id": iid,
                                   "codes": val.get("charges") or val.get("codes")}
            if dec == "confirm" and k:
                fb["confirm"][k] = iid
            if ftype == "sender_authority" and dec in ("confirm", "one_off_approval") and \
                    scope.get("kind") != "one_off":
                cats = val.get("categories") or ([ap.get("category")] if ap.get("category") else [])
                for cat in cats:
                    if ap.get("category") and cat != ap.get("category"):
                        continue
                    fb["authority"].append({"contract": ap.get("contract_id"), "person": ap.get("person") or
                                            subj.get("person"), "category": cat, "from": vf, "to": vt, "id": iid,
                                            "one_off": scope.get("kind") == "one_off", "key": k})
            if ftype == "rate_accepted":
                fb["accepted"].append({"contract": ap.get("contract_id") or subj.get("contract_id"),
                                       "lane": ap.get("lane") or subj.get("lane"), "sent_by": val.get("sent_by"),
                                       "sent_on": parse_date(val.get("sent_on") or ""),
                                       "from": parse_date(val.get("effective_from") or "") or vf,
                                       "to": parse_date(val.get("effective_to") or "") or vt, "id": iid})
            if ftype == "precedence" and dec in ("resolve_conflict", "confirm"):
                fb["precedence"].append({"contract": ap.get("contract_id") or subj.get("contract_id"),
                                         "lane": ap.get("lane") or subj.get("lane"), "use": str(val.get("use") or ""),
                                         "amendment": val.get("amendment"), "from": vf, "id": iid})
    return fb
