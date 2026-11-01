"""The auditors. Each works from the fact model and the glossary's rules and returns items with verdict, amount,
evidence, and the facts the gating and confidence steps need (where the pickup date came from, which authority
each term rests on, which feedback was used, which documents decided it)."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from decimal import Decimal

from .emails import GENERIC_LABELS
from .model import World
from .util import (ZERO, D, add_months, month_key, norm_invoice, quarter_bounds, quarter_of, r2, squash)

INVOICE_CLASSES = ("overcharge", "duplicate", "waived_but_billed", "late_fee_not_owed")
NOT_QS = {"DUTY", "TAX", "LATE"}
TOL = Decimal("0.005")


def key_of(it: dict) -> tuple:
    if it["class"] == "cashback":
        return ("cashback", it["contract_id"], it["period"], it["credit_type"])
    if it["class"] == "expiry":
        return ("expiry", it["contract_id"])
    return (it["class"], norm_invoice(it["invoice_id"]))


def code_of(desc: str, c: dict | None) -> str | None:
    d = squash(re.sub(r"\(\s*(?:mawb|ref)[^)]*\)", "", desc or "", flags=re.I))
    if not d:
        return None
    labels = dict(GENERIC_LABELS)
    if c:
        labels.update(c.get("labels", {}))
        for code, a in c.get("acc", {}).items():
            labels.setdefault(a["name"].lower(), code)
    sq = {squash(k): v for k, v in labels.items()}
    if d in sq:
        return sq[d]
    best = None
    for k, v in sq.items():
        if len(k) >= 4 and (d.startswith(k) or k in d) and (best is None or len(k) > len(best[0])):
            best = (k, v)
    if best:
        return best[1]
    if re.search(r"duty|disbursement", d):
        return "DUTY"
    if re.search(r"^(tax|gst|vat)", d):
        return "TAX"
    if "late" in d:
        return "LATE"
    if "freight" in d:
        return "FRT"
    if "fuel" in d or d.startswith("fsc"):
        return "FUEL"
    return None


def to_usd(amount: Decimal, currency: str, c: dict | None, inv_date: date | None):
    """-> (usd, fx evidence or None, missing: bool). One conversion per finding."""
    if (currency or "USD") == "USD":
        return r2(amount), None, False
    if not c or not inv_date:
        return None, None, True
    row = c.get("fx", {}).get(month_key(inv_date))
    if not row:
        return None, None, True
    return r2(amount / row["rate"]), row["ev"], False


class Auditor:
    def __init__(self, w: World, fb: dict | None = None):
        self.w = w
        self.fb = fb or {}
        self.items: list = []
        self.disputed_native: dict = {}      # invoice key -> amount we dispute (invoice currency)
        self.lane_conflicts: set = set()

    # ==== authority ===================================================================================================
    def authorised(self, c, addr, name, cat, on: date | None, used: set):
        """Is the sender a carrier-side Schedule 1 person for `cat` on contract c (or made one by feedback)?"""
        p = self.w.person(c, addr=addr) or (self.w.person(c, name=name) if not addr else None)
        if p and cat in p["cats"] and p.get("carrier"):
            return p
        for a in self.fb.get("authority", []):
            ok_c = not a.get("contract") or a["contract"] == c["contract_id"]
            ok_p = squash(a.get("person") or "") in (squash(name or ""), squash((addr or "").split("@")[0]
                                                                              .replace(".", "")))
            ok_d = on is None or ((not a.get("from") or a["from"] <= on) and (not a.get("to") or on <= a["to"]))
            if ok_c and ok_p and a.get("category") == cat and ok_d:
                used.add(a["id"])
                return {"name": name, "email": addr, "cats": [cat], "fb": a["id"], "ev": None,
                        "party": c.get("carrier_party")}
        return None

    def contract_for_sender(self, addr):
        for c in self.w.contracts.values():
            if self.w.person(c, addr=addr):
                yield c

    # ==== rates =======================================================================================================
    @staticmethod
    def _period_index(periods, d):
        if not periods or d is None:
            return 0
        for i, p in enumerate(periods):
            if p["start"] and p["end"] and p["start"] <= d <= p["end"]:
                return i
        if periods[-1]["end"] and d > periods[-1]["end"]:
            return len(periods) - 1
        return 0

    @staticmethod
    def _pick(rows, lane, w, idx):
        band = mn = None
        cands = [r for r in rows if r["lane"] == lane]
        for r in cands:
            rate = r["rates"][min(idx, len(r["rates"]) - 1)]
            if r["kind"] == "min":
                mn = (rate, r["ev"])
            elif w is not None and r["lo"] is not None and r["lo"] <= w and (r["hi"] is None or w <= r["hi"]):
                band = (rate, r["ev"])
        if band is None and w is not None:
            below = [r for r in cands if r["kind"] == "band" and r["lo"] is not None and r["lo"] <= w]
            if below:
                r = max(below, key=lambda r: r["lo"])
                band = (r["rates"][min(idx, len(r["rates"]) - 1)], r["ev"])
        return band, mn

    def notices(self, c):
        """Rate notices for this contract held as emails: [(notice, sender person or None, email facts)]."""
        cache = self.__dict__.setdefault("_cache_notices", {})
        if c["contract_id"] not in cache:
            cache[c["contract_id"]] = self._notices(c)
        return cache[c["contract_id"]]

    def _notices(self, c):
        out = []
        for f in self.w.emails:
            rec = self.w.rec_by_doc[f["doc"]]
            for n in f["notices"]:
                ref_ok = n.get("ref") and c["contract_id"] in n["ref"]
                lanes_ok = any(r["lane"] in {x["lane"] for x in c["rates"]} for r in n["rows"])
                if not (ref_ok or (lanes_ok and self.w.person(c, addr=rec["from_addr"]))):
                    if not (ref_ok or lanes_ok):
                        continue
                    if not any(cc is c for cc in self.contract_for_sender(rec["from_addr"])) and not ref_ok:
                        continue
                out.append((n, rec, f))
        return out

    def rate(self, c, lane, w, pickup, used: set) -> dict:
        res = {"status": "unknown", "rate": None, "min": None, "ev": [], "alts": [], "missing": None,
               "authority": 1, "decisive": []}
        idx = self._period_index(c["periods"], pickup)
        band, mn = self._pick(c["rates"], lane, w, idx)
        src = None
        if band:
            src = {"rate": band[0], "min": mn[0] if mn else None, "ev": [band[1]], "label": "MSA Annex A",
                   "eff": c.get("effective"), "doc": c["doc"]}
        # amendments and referenced annexes, latest effective first wins
        amend = None
        for a in c.get("amendments", []):
            if not a.get("signed"):
                continue
            eff = a.get("effective")
            if a["rates"] and (eff is None or pickup is None or eff <= pickup):
                b2, m2 = self._pick(a["rates"], lane, w, 0)
                if b2:
                    cand = {"rate": b2[0], "min": m2[0] if m2 else (src or {}).get("min"), "ev": [b2[1]],
                            "label": f"amendment {a.get('ref') or a['doc']}", "eff": eff, "doc": a["doc"],
                            "amend": a}
                    if amend is None or (eff or date.min) >= (amend["eff"] or date.min):
                        amend = cand
            for ref in a.get("annex_refs", []):
                if lane in ref["lanes"] and (ref["from"] is None or pickup is None or ref["from"] <= pickup):
                    held = next((x for x in c.get("annex_docs", []) if squash(ref["name"]) in squash(x.get("name") or "")),
                                None)
                    if held:
                        b3, m3 = self._pick(held["rates"], lane, w, self._period_index(held["periods"], pickup))
                        if b3:
                            cand = {"rate": b3[0], "min": m3[0] if m3 else None, "ev": [b3[1], ref["ev"]],
                                    "label": ref["name"], "eff": ref["from"], "doc": held["doc"], "amend": a}
                            if amend is None or (ref["from"] or date.min) >= (amend["eff"] or date.min):
                                amend = cand
                    elif amend is None or (ref["from"] or date.min) >= (amend["eff"] or date.min):
                        amend = {"missing": ref, "eff": ref["from"], "amend": a, "doc": a["doc"]}
        if amend and amend.get("missing"):
            rates_p = self.w.contact(c, "rates")
            ref = amend["missing"]
            res.update(status="missing", ev=[ref["ev"]], decisive=[amend["doc"]],
                       missing={"name": ref["name"], "holder": _holder(c, rates_p), "email": (rates_p or {}).get("email")})
            return res
        base = amend or src
        # authorised rate notices in force on the pickup date
        notice = None
        for n, rec, f in self.notices(c):
            if lane not in n["lanes"] or n["quoted"]:
                continue
            nd = _received(rec)
            if n["from"] and pickup and pickup < n["from"]:
                continue
            if n["to"] and pickup and pickup > n["to"]:
                continue
            accepted = self._accepted(c, lane, rec, pickup, used)
            who = self.authorised(c, rec["from_addr"], rec["from_name"], "rates", nd, used) if not f["injection"] \
                else None
            if not (who or accepted):
                continue
            b4, m4 = self._pick(n["rows"], lane, w, 0)
            if not b4:
                continue
            cand = {"rate": b4[0], "min": m4[0] if m4 else (base or {}).get("min"), "ev": [b4[1]],
                    "label": f"rate notice from {rec['from_name']}", "eff": n["from"], "doc": rec["doc"],
                    "sent": nd, "fb": accepted}
            if notice is None or nd >= notice["sent"]:
                notice = cand
        if notice and notice.get("fb"):
            used.add(notice["fb"])
        if not base and not notice:
            return res
        chosen = base or notice
        if notice and base:
            if base is src or abs(base["rate"] - notice["rate"]) <= TOL:
                chosen = notice if base is src else base
                if notice.get("fb") and base is not src and abs(base["rate"] - notice["rate"]) > TOL:
                    chosen = notice
            elif notice.get("fb"):
                chosen = notice
            else:
                prec = self._precedence(c, lane, pickup, used)
                if prec == "amendment":
                    chosen = base
                elif prec == "notice":
                    chosen = notice
                elif c["params"].get("unranked_notice_vs_amendment"):
                    res.update(status="conflict", alts=[base, notice], ev=base["ev"] + notice["ev"]
                               + ([c["ev"]["unranked"]] if c["ev"].get("unranked") else []),
                               decisive=[base["doc"], notice["doc"]], authority=2)
                    return res
                elif c["params"].get("notice_prevails"):
                    chosen = notice
                else:
                    chosen = base
        res.update(status="ok", rate=chosen["rate"], min=chosen.get("min"), ev=list(chosen["ev"]),
                   decisive=[chosen["doc"]], authority=2 if chosen is notice else 1, label=chosen["label"],
                   src_doc=chosen["doc"])
        if chosen is notice and c["ev"].get("notices"):
            res["ev"].append(c["ev"]["notices"])
        return res

    def _accepted(self, c, lane, rec, pickup, used):
        for a in self.fb.get("accepted", []):
            if a["contract"] != c["contract_id"] or a["lane"] != lane:
                continue
            if a.get("from") and pickup and pickup < a["from"]:
                continue
            if a.get("to") and pickup and pickup > a["to"]:
                continue
            if a.get("sent_by") and squash(a["sent_by"]) != squash(rec["from_name"]):
                continue
            if a.get("sent_on") and a["sent_on"] != _received(rec):
                continue
            return a["id"]
        return None

    def _precedence(self, c, lane, pickup, used):
        for p in self.fb.get("precedence", []):
            if p["contract"] == c["contract_id"] and (not p.get("lane") or p["lane"] == lane) and \
                    (not p.get("from") or not pickup or p["from"] <= pickup):
                used.add(p["id"])
                return "notice" if re.search(r"notice", p["use"], re.I) else "amendment"
        return None

    # ==== waivers =====================================================================================================
    def waivers(self, c, used: set) -> list:
        cache = self.__dict__.setdefault("_cache_waivers", {})
        if c["contract_id"] not in cache:
            cache[c["contract_id"]] = self._waivers(c)
        return cache[c["contract_id"]]

    def _waivers(self, c) -> list:
        out = []
        for f in self.w.emails:
            rec = self.w.rec_by_doc[f["doc"]]
            for wv in f["waivers"]:
                if wv["quoted"]:
                    p = self.w.person(c, addr=wv.get("author_addr")) or self.w.person(c, name=wv.get("author"))
                    if not p or "waivers" not in p["cats"] or not p.get("carrier"):
                        continue
                    orig = any(g["doc"] != f["doc"] and any(not x["quoted"] and x["codes"] == wv["codes"]
                                                             and x["awbs"] == wv["awbs"] for x in g["waivers"])
                               and self.w.rec_by_doc[g["doc"]]["from_addr"] == p["email"]
                               for g in self.w.emails)
                    if orig:
                        continue
                    out.append({**wv, "doc": f["doc"], "status": "quoted", "person": p, "sent": _received(rec)})
                    continue
                who = self.authorised(c, rec["from_addr"], rec["from_name"], "waivers", _received(rec), set())
                if who and who.get("fb"):
                    wv = {**wv, "fb": who["fb"]}
                out.append({**wv, "doc": f["doc"], "status": "ok" if who else "unauthorised", "person": who,
                            "sent": _received(rec), "sender": rec["from_name"]})
        return out

    @staticmethod
    def waiver_covers(wv, ln, code, inv_key):
        if code not in wv["codes"]:
            return False
        if wv["awbs"]:
            return ln.get("awb") in wv["awbs"] or (ln.get("mawb") in wv["awbs"])
        if wv["months"]:
            m = ln.get("month_ref") or (month_key(ln["date"]) if ln.get("date") else None)
            return m in wv["months"]
        if wv["invoices"]:
            return inv_key in {norm_invoice(x) for x in wv["invoices"]}
        return False

    def condition_met(self, wv, inv_key):
        if not wv.get("conditional"):
            return True
        d = wv.get("condition_date")
        row = self.w.ledger_row(inv_key)
        if d and row and row.get("paid") and "paid" in row["status"]:
            return row["paid"] <= d
        return False

    # ==== invoice lines ===============================================================================================
    def run(self):
        """Every auditor runs on its own: a document we can't make sense of costs that key, never the report."""
        used_global: set = set()
        self.dups = _guard(self.line_duplicates, "duplicate index") or {}
        for k in sorted(self.w.invoices, key=lambda k: (self.w.invoices[k]["inv"].get("date") or date.min, k)):
            _guard(self.audit_invoice, f"invoice {k}", k)
        _guard(self.resends, "resends")
        for k in list(self.w.invoices):
            _guard(self.late_fees, f"late fees {k}", k)
        for k in list(self.w.invoices):
            _guard(self.late_fee_risk, f"late-fee risk {k}", k)
        for c in self.w.contracts.values():
            for fn in (self.rebates, self.improvement, self.margin_step, self.expiry):
                _guard(fn, f"{fn.__name__} {c.get('contract_id')}", c)
        return self.items, used_global

    def audit_invoice(self, k):
        ent = self.w.invoices[k]
        inv = ent["inv"]
        c = self.w.contract(inv.get("contract_id"))
        used: set = set()
        groups: dict = {}
        for ln in inv["lines"]:
            if ln.get("tax"):
                continue
            ln["code"] = "LATE" if ln.get("late") else code_of(ln["desc"], c)
            groups.setdefault(ln.get("awb") or f"_{ln.get('month_ref') or ''}", []).append(ln)
        res = {"over": [], "waived": [], "dup": [], "missing": None, "conflict": False, "ev": [], "ev_wv": [],
               "ev_dup": [], "pickup_src": set(), "authority": 1, "decisive": set(), "quoted_wv": None,
               "lanes": set()}
        wvs = self.waivers(c, used) if c else []
        for awb, lines in groups.items():
            self.audit_group(c, inv, k, awb, lines, wvs, res, used)
        cur = inv.get("currency") or (c or {}).get("currency") or "USD"
        base = {"invoice_id": inv["id"], "contract_id": inv.get("contract_id"), "currency": cur,
                "inv_date": inv.get("date"), "doc": inv["doc"], "ocr": inv.get("scan"), "fb_used": used,
                "received": inv["received"], "reconciles": inv.get("reconciles")}
        # credit notes against this invoice settle what they cover (a credit that cancels the charge is no finding)
        credit, cev = self.invoice_credits(k)
        if credit > 0 and (res["waived"] or res["over"]):
            res["ev"] += cev
            res["ev_wv"] += cev
        # waived but billed
        if res["waived"]:
            amt = sum(x for x, _ in res["waived"])
            used_c = min(credit, amt)
            amt, credit = amt - used_c, credit - used_c
            if amt > TOL:
                self.emit_money("waived_but_billed", base, amt, c, res["ev_wv"], res, extra={"variant": "WB"})
        elif res["quoted_wv"]:
            wv, ln = res["quoted_wv"]
            p = wv["person"]
            dt = wv.get("quoted_date") or ""
            name = (f"original email from {p['name']} dated {dt} waiving the {', '.join(wv['codes'])} "
                    f"on AWB {', '.join(wv['awbs'])} (quoted in {wv['doc']})")
            self.emit_cd("waived_but_billed", base, c, [wv["ev"], ln["ev"]], name, _holder(c, p), p["email"], res)
        # duplicates (lines billed on an earlier invoice)
        if res["dup"]:
            amt = sum(x for x, _ in res["dup"])
            self.emit_money("duplicate", base, amt, c, res["ev_dup"], res, extra={"variant": "DU-line"})
        # overcharge
        if res["missing"]:
            m = res["missing"]
            self.emit_cd("overcharge", base, c, res["ev"] + m["ev"], m["name"], m["holder"], m.get("email"), res)
        elif res["conflict"]:
            lo = sum(x["low"] for x in res["over"])
            hi = sum(x["high"] for x in res["over"])
            self.emit_conflict(base, c, lo, hi, res)
        elif res["over"]:
            amt = sum(x["low"] for x in res["over"]) - credit
            if amt > TOL:
                self.emit_money("overcharge", base, amt, c, res["ev"], res, extra={"variant": "OC"})

    def invoice_credits(self, k):
        """Billing-correction credit notes that name this invoice (rebate and promised credits excluded), in the
        invoice's own currency, with their evidence."""
        tot, ev = ZERO, []
        for cn in self.w.credit_notes:
            if re.search(r"rebate|improvement|margin", cn.get("credit_type") or ""):
                continue
            if k not in {norm_invoice(r) for r in cn.get("ref_invoices", [])}:
                continue
            tot += cn.get("total") or ZERO
            q = cn.get("ev_total") or (cn["lines"][0]["ev"] if cn.get("lines") else None)
            if q:
                ev.append(q)
        return tot, ev

    def audit_group(self, c, inv, k, awb, lines, wvs, res, used):
        w = self.w
        real = not awb.startswith("_")
        frt = next((ln for ln in lines if ln["code"] == "FRT"), None)
        line_date = next((ln["date"] for ln in lines if ln.get("date")), None)
        pk, pk_src, pk_ev = w.pickup(awb, line_date) if real else (line_date, "invoice", None)
        weight = frt["qty"] if frt and frt.get("unit") == "kg" else next(
            (ln["qty"] for ln in lines if ln.get("unit") == "kg"), None)
        awbdoc = w.awbs.get(awb)
        w_ev = None
        if awbdoc and awbdoc.get("chargeable") and weight and awbdoc["chargeable"] < weight:
            weight, w_ev = awbdoc["chargeable"], awbdoc.get("ev_weight")
        lane = (frt or {}).get("lane") or next((ln["lane"] for ln in lines if ln.get("lane")), None)
        if not lane and awbdoc and awbdoc.get("origin"):
            lane = f"{awbdoc['origin']}-{awbdoc['dest']}"
        freight = None          # {"low": x, "high": y} correct freight
        rr = None
        for ln in lines:
            code = ln["code"]
            billed = ln.get("amount")
            if billed is None or code in ("TAX", "DUTY", "LATE") or billed <= 0:
                continue
            # waivers first: a waived line is waived_but_billed, never an overcharge
            hit = next((wv for wv in wvs if wv["status"] == "ok" and self.waiver_covers(wv, ln, code, k)
                        and self.condition_met(wv, k)), None)
            if hit:
                res["waived"].append((billed, ln))
                res["ev_wv"] += [hit["ev"], ln["ev"]]
                if hit.get("fb"):
                    used.add(hit["fb"])
                if hit["person"] and hit["person"].get("ev"):
                    res["ev_wv"].append(hit["person"]["ev"])
                if c and c["ev"].get("waiver"):
                    res["ev_wv"].append(c["ev"]["waiver"])
                res["authority"] = max(res["authority"], 3 if hit.get("llm") else 2)
                res["decisive"].add(hit["doc"])
                continue
            q = next((wv for wv in wvs if wv["status"] == "quoted" and self.waiver_covers(wv, ln, code, k)), None)
            if q and not res["quoted_wv"]:
                res["quoted_wv"] = (q, ln)
                continue
            dup = self.dups.get(id(ln))
            if dup:
                res["dup"].append((dup["amount"], ln))
                res["ev_dup"] += [dup["first"]["ev"], ln["ev"]]
                if c and code in c.get("label_ev", {}):
                    res["ev_dup"].append(c["label_ev"][code])
                continue
            if not c:
                continue
            corr, ev = None, []
            if code == "FRT" and ln.get("unit") == "kg" and lane:
                rr = self.rate(c, lane, weight, pk, used)
                if rr["status"] == "missing":
                    res["missing"] = res["missing"] or rr["missing"] | {"ev": rr["ev"]}
                    res["ev"].append(ln["ev"])
                    res["decisive"].update(rr["decisive"])
                    continue
                if rr["status"] == "unknown":
                    continue
                lane_key = (c["contract_id"], lane, self._period_index(c["periods"], pk))
                res["lanes"].add(lane_key)
                if rr["status"] == "conflict":
                    vals = [max(r2(a["rate"] * weight), a["min"] or ZERO) for a in rr["alts"]]
                    freight = {"low": min(vals), "high": max(vals)}
                    res["conflict"] = True
                    self.lane_conflicts.add(lane_key)
                else:
                    v = max(r2(rr["rate"] * weight), rr["min"] or ZERO)
                    freight = {"low": v, "high": v}
                    res["authority"] = max(res["authority"], rr["authority"])
                res["decisive"].update(rr["decisive"])
                corr = freight
                ev = rr["ev"] + ([c["ev"]["pricing"]] if c["ev"].get("pricing") else [])
                if w_ev:
                    ev.append(w_ev)
            elif code == "FUEL":
                f = c["fuel"].get(month_key(pk)) if pk else None
                if not f:
                    continue
                b = freight if freight else ({"low": ln["base"], "high": ln["base"]} if ln.get("base") else None)
                if not b:
                    continue
                corr = {"low": r2(f["pct"] * b["high"] / 100), "high": r2(f["pct"] * b["low"] / 100)}
                corr = {"low": min(corr.values()), "high": max(corr.values())}
                ev = [f["ev"]] + ([c["ev"]["fuel"]] if c["ev"].get("fuel") else [])
            else:
                v, ev = self.accessorial(c, code, ln, weight, pk, awb if real else None)
                if v is None:
                    continue
                corr = {"low": v, "high": v}
            # low = billed - highest correct ; high = billed - lowest correct
            lo = max(ZERO, billed - corr["high"])
            hi = max(ZERO, billed - corr["low"])
            if hi > TOL:
                res["over"].append({"low": lo, "high": hi, "line": ln})
                res["ev"] += [ln["ev"]] + ev
                if pk_ev:
                    res["ev"].append(pk_ev)
                res["pickup_src"].add(pk_src if real else "n/a")

    def accessorial(self, c, code, ln, weight, pk, awb):
        """Correct amount for one accessorial line, or (None, []) when it can't be checked."""
        a = c["acc"].get(code)
        if not a:
            return None, []
        fee = a["fee"]
        basis = a["basis"].lower()
        ev = [a["ev"]]
        q = ln.get("qty") or Decimal(1)
        if code == "DG" and awb:
            info = self.w.un_info(awb)
            if info is None:
                return None, []
            ev += info["ev"] + [x for x in (c["ev"].get("dryice"), c["ev"].get("mixed")) if x]
            return (fee if info["dg"] else ZERO), ev
        if code == "DRYICE" and awb:
            info = self.w.un_info(awb)
            if info is None:
                return min(ln["amount"], fee), ev
            ev += info["ev"] + [x for x in (c["ev"].get("dryice"), c["ev"].get("mixed")) if x]
            return (fee if info["dryice"] and not info["dg"] else ZERO), ev
        if code == "STOR" and awb:
            arr, rel = self.w.release(awb)
            free = c["params"].get("free_days")
            if not arr or not rel or free is None:
                return None, []
            days = max(0, (rel["date"] - arr["date"]).days - free)
            ev += [arr["ev"], rel["ev"]] + ([c["ev"]["storage"]] if c["ev"].get("storage") else [])
            return r2(fee * days), ev
        if code == "DLV" and awb:
            evs = self.w.events(awb)
            if any(e["event"] == "COLLECTED_BY_CONSIGNEE" for e in evs) and not any(e["event"] == "DELIVERED"
                                                                                  for e in evs):
                e = next(e for e in evs if e["event"] == "COLLECTED_BY_CONSIGNEE")
                return ZERO, ev + [e["ev"]] + ([c["ev"]["delivery"]] if c["ev"].get("delivery") else [])
            return fee, ev
        if code == "CUSTOMS" and awb:
            reasons = [e for e in self.w.events(awb) if e["event"] in ("CUSTOMS_HOLD", "HELD", "CUSTOMS_EXAM")]
            txt = " ".join((e.get("reason") or "") for e in reasons)
            for f in self.w.emails:
                for h in f["holds"]:
                    if h["awb"] == awb:
                        txt += " " + (h.get("reason") or "") + " " + h.get("reason_text", "")
                        reasons.append(h)
            if re.search(r"CARRIER|carrier(?:'s)? (?:error|omission|fault|mistake)|error (?:by|of) the carrier|"
                         r"AWB[_ ](?:DATA[_ ])?ERROR|MANIFEST[_ ]ERROR", txt):
                return ZERO, ev + [r["ev"] for r in reasons] + ([c["ev"]["customs"]] if c["ev"].get("customs") else [])
            return fee, ev
        if code == "PEAK":
            months = a.get("months")
            if months and pk and pk.month not in months:
                return ZERO, ev
            return (r2(fee * weight) if weight else None), ev
        if "per chargeable kg" in basis or "per kg" in basis:
            return (r2(fee * weight) if weight else None), ev
        if "per shipment" in basis:
            return fee, ev
        if "per month" in basis or "per storage day" in basis or "per day" in basis:
            return r2(fee * q), ev
        return None, []

    # ==== duplicates ==================================================================================================
    def line_duplicates(self) -> dict:
        """id(line) -> {first line, amount} for lines billed again on a later invoice (same AWB, code, amount)."""
        seen: dict = {}
        out: dict = {}
        order = sorted(self.w.invoices.values(), key=lambda e: (e["inv"].get("date") or date.min,
                                                                 e["inv"]["received"]))
        credits = self.w.credit_notes
        for e in order:
            inv = e["inv"]
            c = self.w.contract(inv.get("contract_id"))
            for ln in inv["lines"]:
                if ln.get("tax") or ln.get("amount") in (None, ZERO):
                    continue
                code = "LATE" if ln.get("late") else code_of(ln["desc"], c)
                if code in ("DUTY", "TAX"):
                    continue
                if ln.get("late"):
                    key = ("LATE", norm_invoice(ln["late"]["ref_invoice"]), ln["late"]["month"])
                elif ln.get("awb"):
                    key = (ln["awb"], code, ln["amount"])
                elif ln.get("month_ref"):
                    key = (inv.get("contract_id"), code, ln["month_ref"], ln["amount"])
                else:
                    continue
                if key in seen and seen[key][0] != inv["key"]:
                    first = seen[key][1]
                    credit = sum((cn.get("total") or ZERO) for cn in credits
                                 if ln.get("awb") in cn.get("awbs", []) and
                                 any(norm_invoice(r) in (inv["key"], seen[key][0]) for r in cn.get("ref_invoices", [])))
                    amt = ln["amount"] - credit
                    if amt > TOL:
                        out[id(ln)] = {"first": first, "amount": amt}
                else:
                    seen.setdefault(key, (inv["key"], ln))
        return out

    def resends(self):
        for k, ent in self.w.invoices.items():
            if not ent["copies"]:
                continue
            row = self.w.ledger_row(k)
            if not row or not re.search(r"paid|scheduled", row["status"]) or re.search(r"unpaid|part", row["status"]):
                continue
            for cp in ent["copies"]:
                f = cp["facts"]
                if not f or not f["pay_request"] or f["copy_marked"] or cp["inv"].get("copy_marked"):
                    continue
                inv = ent["inv"]
                c = self.w.contract(inv.get("contract_id"))
                total = inv.get("total") or row.get("amount")
                if total is None:
                    continue
                credit, cev = self.invoice_credits(k)
                total -= credit
                if total <= TOL:
                    break
                pay_line = next((s for s in f["own"] if re.search(r"payment|pay|outstanding", s, re.I)), None)
                ev = [row["ev"]]
                if pay_line:
                    ev.append({"doc": f["doc"], "page": 1, "quote": " ".join(pay_line.split())})
                base = {"invoice_id": inv["id"], "contract_id": inv.get("contract_id"),
                        "currency": inv.get("currency") or "USD", "inv_date": inv.get("date"), "doc": inv["doc"],
                        "ocr": inv.get("scan"), "fb_used": set(), "received": cp["inv"]["received"],
                        "reconciles": True}
                existing = next((i for i in self.items if i["class"] == "duplicate" and
                                 norm_invoice(i["invoice_id"]) == k), None)
                if existing:
                    self.items.remove(existing)
                res = {"pickup_src": {"n/a"}, "authority": 1, "decisive": {cp["inv"]["doc"], row["ev"]["doc"]},
                       "lanes": set()}
                self.emit_money("duplicate", base, total, c, ev, res, extra={"variant": "DU-resend"})
                break

    # ==== late fees ===================================================================================================
    def due_date(self, k, c, used):
        """(due date, evidence list, extended?) using an authorised extension when there is one."""
        row = self.w.ledger_row(k)
        ent = self.w.invoices.get(k)
        due = (row or {}).get("due") or (ent["inv"].get("due") if ent else None)
        ev = [row["ev"]] if row else []
        ext = None
        for f in self.w.emails:
            rec = self.w.rec_by_doc[f["doc"]]
            for x in f["extensions"]:
                if k not in {norm_invoice(i) for i in x["invoices"]} or x.get("conditional"):
                    continue
                cc = c or next(iter(self.contract_for_sender(rec["from_addr"])), None)
                if cc and self.authorised(cc, rec["from_addr"], rec["from_name"], "payment_terms", _received(rec),
                                          used):
                    if ext is None or _received(rec) >= ext[1]:
                        ext = (x, _received(rec))
        if ext:
            due = ext[0]["new_due"]
            ev = [ext[0]["ev"]] + ev
            if c and c["ev"].get("extension"):
                ev.append(c["ev"]["extension"])
        return due, ev, ext is not None

    def late_fees(self, k):
        ent = self.w.invoices[k]
        inv = ent["inv"]
        c = self.w.contract(inv.get("contract_id"))
        if not c:
            return
        p = c["params"]
        used: set = set()
        total, ev = ZERO, []
        decisive = set()
        for ln in inv["lines"]:
            lt = ln.get("late")
            if not lt or ln.get("amount") is None:
                continue
            rk = norm_invoice(lt["ref_invoice"])
            row = self.w.ledger_row(rk)
            if not row:
                continue
            due, dev, _ = self.due_date(rk, c, used)
            if not due:
                continue
            paid = row.get("paid") if "paid" in row["status"] and "unpaid" not in row["status"] else None
            stop = paid or inv.get("date")
            if not stop:
                continue
            days = (stop - due).days
            grace = p.get("grace_days") or 0
            pct = p.get("late_pct") or lt["pct"]
            if days <= grace:
                correct = ZERO
            else:
                n = math.ceil(days / 30)
                principal = (row.get("amount") or lt["principal"]) - self.disputed_native.get(rk, ZERO)
                fees, basev = [], principal
                for i in range(1, n + 1):
                    fi = r2(pct * basev / 100)
                    fees.append(fi)
                    if p.get("compound"):
                        basev = basev + fi
                correct = fees[lt["month"] - 1] if lt["month"] <= n else ZERO
            diff = ln["amount"] - correct
            if diff > TOL:
                total += diff
                ev += [ln["ev"]] + dev + ([c["ev"]["late"]] if c["ev"].get("late") else [])
                decisive.update(e["doc"] for e in dev)
        if total > TOL:
            base = {"invoice_id": inv["id"], "contract_id": inv.get("contract_id"),
                    "currency": inv.get("currency") or c.get("currency"), "inv_date": inv.get("date"),
                    "doc": inv["doc"], "ocr": inv.get("scan"), "fb_used": used, "received": inv["received"],
                    "reconciles": inv.get("reconciles")}
            self.emit_money("late_fee_not_owed", base, total, c, ev, {"pickup_src": {"n/a"}, "authority": 2,
                                                                      "decisive": decisive, "lanes": set()},
                            extra={"variant": "LF"})

    def late_fee_risk(self, k):
        ent = self.w.invoices[k]
        inv = ent["inv"]
        c = self.w.contract(inv.get("contract_id"))
        if not c:
            return
        row = self.w.ledger_row(k)
        reg = self.w.latest_register()
        if row:
            st = row["status"]
            if re.search(r"\bpaid\b|scheduled|settled|cancel|void", st) and not re.search(r"unpaid|part", st):
                return
        elif not reg or reg["received"] >= inv["received"]:
            return
        used: set = set()
        due, ev, _ = self.due_date(k, c, used)
        if not due or not (0 < (due - self.w.as_of).days <= 14):
            return
        pct = c["params"].get("late_pct")
        if not pct:
            return
        principal = ((row or {}).get("amount") or inv.get("total") or ZERO) - self.disputed_native.get(k, ZERO)
        fee = r2(pct * principal / 100)
        cur = inv.get("currency") or c.get("currency") or "USD"
        usd, fx_ev, missing = to_usd(fee, cur, c, inv.get("date"))
        ev = ev + ([c["ev"]["late"]] if c["ev"].get("late") else [])
        if missing:
            return
        if fx_ev:
            ev.append(fx_ev)
        self.items.append({"class": "late_fee_risk", "invoice_id": inv["id"], "contract_id": c["contract_id"],
                           "verdict": "finding", "amount": usd, "deadline": due, "evidence": ev,
                           "pickup_src": {"n/a"}, "authority": 1, "fb_used": used, "decisive": set(),
                           "ocr": inv.get("scan"), "kind": "internal", "variant": "LF-risk",
                           "reconciles": inv.get("reconciles")})

    # ==== cashback ====================================================================================================
    def qs_of(self, c, start, end):
        """Qualifying Spend in USD over invoices dated in [start, end], net of credit notes dated in it."""
        qs, ok, evs = ZERO, True, []
        qs_text = (c["clauses"].get("1.1") or {}).get("text", "") + " " + (c["ev"].get("qs") or {}).get("quote", "")
        extra = {"COST", "MARGIN", "HDL"} if re.search(r"carrier cost|margin|special handling", qs_text, re.I) \
            else set()
        incl = {"FRT", "FUEL", "SEC"} | set(c.get("acc", {})) | extra
        for k, ent in self.w.invoices.items():
            inv = ent["inv"]
            if inv.get("contract_id") != c["contract_id"] or not inv.get("date") or not (start <= inv["date"] <= end):
                continue
            amt = ZERO
            for ln in inv["lines"]:
                if ln.get("tax") or ln.get("amount") is None:
                    continue
                code = "LATE" if ln.get("late") else code_of(ln["desc"], c)
                if code in incl and code not in NOT_QS:
                    amt += ln["amount"]
            usd, _, missing = to_usd(amt, inv.get("currency") or c.get("currency"), c, inv["date"])
            if missing:
                ok = False
                continue
            qs += usd
            if not inv.get("reconciles"):
                ok = False
        for cn in self.w.credit_notes:
            if cn.get("contract_id") != c["contract_id"] or not cn.get("date") or not (start <= cn["date"] <= end):
                continue
            if re.search(r"rebate|improvement|margin", cn.get("credit_type") or ""):
                continue
            usd, _, missing = to_usd(cn.get("total") or ZERO, cn.get("total_currency") or cn.get("currency"), c,
                                     cn["date"])
            if not missing:
                qs -= usd
        return qs, ok

    def credited(self, c, period, kind):
        tot, ev = ZERO, []
        for cn in self.w.credit_notes:
            if cn.get("contract_id") != c["contract_id"] or (cn.get("period") or "").upper() != period:
                continue
            if kind not in (cn.get("credit_type") or ""):
                continue
            usd, _, _ = to_usd(cn.get("total") or ZERO, cn.get("total_currency") or "USD", c, cn.get("date"))
            tot += usd or ZERO
            ev.append(cn.get("ev_total") or {"doc": cn["doc"], "page": 1, "quote": cn["lines"][0]["raw"]}
                      if cn.get("lines") else {"doc": cn["doc"], "page": 1, "quote": cn["id"]})
        return tot, ev

    def rebates(self, c):
        p = c["params"]
        tiers = sorted(p.get("rebate_tiers") or [])
        if not tiers or not c.get("effective"):
            return
        end_c = c.get("end") or self.w.as_of
        q = quarter_of(c["effective"])
        while True:
            qs_, qe = quarter_bounds(q)
            if qe >= self.w.as_of or qs_ > end_c:
                break
            qs, ok = self.qs_of(c, max(qs_, c["effective"]), qe)
            pct = max((t[1] for t in tiers if qs >= t[0]), default=None)
            if pct:
                earned = r2(qs * pct / 100)
                cred, cev = self.credited(c, q, "rebate")
                owed = earned - cred
                if owed > Decimal("0.01"):
                    due = qe + timedelta(days=p.get("rebate_due_days") or 45)
                    ev = [x for x in (c["ev"].get("rebate"), c["ev"].get("qs"), c["ev"].get("rebate_credit")) if x]
                    self.items.append({"class": "cashback", "contract_id": c["contract_id"], "period": q,
                                       "credit_type": "rebate", "verdict": "finding", "amount": owed,
                                       "due": due, "evidence": ev + cev, "pickup_src": {"n/a"}, "authority": 1,
                                       "fb_used": set(), "decisive": set(), "ok": ok, "variant": "CB-rebate",
                                       "reconciles": ok})
            ny, nq = (int(q[:4]), int(q[-1]) + 1)
            q = f"{ny + (nq > 4)}-Q{1 if nq > 4 else nq}"

    def improvement(self, c):
        p = c["params"]
        if not p.get("improvement_pct") or not c.get("effective"):
            return
        n = 1
        while True:
            start = add_months(c["effective"], 12 * (n - 1))
            end = add_months(c["effective"], 12 * n) - timedelta(days=1)
            if end >= self.w.as_of or start > (c.get("end") or self.w.as_of):
                break
            qs, ok = self.qs_of(c, start, end)
            earned = r2(qs * p["improvement_pct"] / 100)
            cred, cev = self.credited(c, f"CY{n}", "improvement")
            owed = earned - cred
            if owed > Decimal("0.01"):
                due = end + timedelta(days=1 + (p.get("improvement_due_days") or 60))
                ev = [x for x in (c["ev"].get("improvement"), c["ev"].get("qs")) if x]
                self.items.append({"class": "cashback", "contract_id": c["contract_id"], "period": f"CY{n}",
                                   "credit_type": "promised_credit", "verdict": "finding", "amount": owed, "due": due,
                                   "evidence": ev + cev, "pickup_src": {"n/a"}, "authority": 1, "fb_used": set(),
                                   "decisive": set(), "ok": ok, "variant": "CB-improvement", "reconciles": ok})
            n += 1

    def margin_step(self, c):
        """A handling margin that should have stepped down once the quarter's spend crossed the threshold."""
        p = c["params"]
        thr, new = p.get("margin_step_threshold"), p.get("margin_step_pct")
        if not thr or new is None or not c.get("effective"):
            return
        per_q: dict = {}
        for k, ent in sorted(self.w.invoices.items(), key=lambda kv: (kv[1]["inv"].get("date") or date.min, kv[0])):
            inv = ent["inv"]
            if inv.get("contract_id") != c["contract_id"] or not inv.get("date"):
                continue
            q = quarter_of(inv["date"])
            st = per_q.setdefault(q, {"spend": ZERO, "crossed": False, "over": ZERO, "ev": []})
            if st["crossed"]:
                for ln in inv["lines"]:
                    if code_of(ln["desc"], c) == "MARGIN" and ln.get("pct") and ln["pct"] > new and ln.get("base"):
                        st["over"] += ln["amount"] - r2(new * ln["base"] / 100)
                        st["ev"].append(ln["ev"])
            amt = sum((ln["amount"] or ZERO) for ln in inv["lines"] if not ln.get("tax") and
                      code_of(ln["desc"], c) not in NOT_QS)
            usd, _, _ = to_usd(amt, inv.get("currency") or "USD", c, inv["date"])
            st["spend"] += usd or ZERO
            if st["spend"] >= thr:
                st["crossed"] = True
        for q, st in per_q.items():
            if quarter_bounds(q)[1] >= self.w.as_of or st["over"] <= TOL:
                continue
            cred, cev = self.credited(c, q, "margin")
            owed = r2(st["over"]) - cred
            if owed > Decimal("0.01"):
                ev = [x for x in (c["ev"].get("step"), c["ev"].get("margin")) if x] + st["ev"]
                self.items.append({"class": "cashback", "contract_id": c["contract_id"], "period": q,
                                   "credit_type": "margin_step_down", "verdict": "finding", "amount": owed,
                                   "due": quarter_bounds(q)[1], "evidence": ev + cev, "pickup_src": {"n/a"},
                                   "authority": 1, "fb_used": set(), "decisive": set(), "ok": True,
                                   "variant": "CB-margin", "reconciles": True})

    # ==== expiry ======================================================================================================
    def expiry(self, c):
        end = c.get("end")
        ev = [x for x in (c["ev"].get("term"),) if x]
        decisive = set()
        for a in c.get("amendments", []):
            if a.get("signed") and a.get("term_end"):
                end = a["term_end"]
                ev = [a["ev"].get("term") or a["ev"].get("effective")]
                decisive.add(a["doc"])
        if not end:
            return
        if c.get("auto_renew"):
            if c["ev"].get("renewal"):
                ev.append(c["ev"]["renewal"])
            nd = c.get("notice_days") or 0
            op = c.get("notice_date") or (end - timedelta(days=nd))
            while op < self.w.as_of:
                end = add_months(end + timedelta(days=1), c.get("renew_months") or 12) - timedelta(days=1)
                op = end - timedelta(days=nd)
        else:
            op = end
        if 0 <= (op - self.w.as_of).days <= 60:
            self.items.append({"class": "expiry", "contract_id": c["contract_id"], "verdict": "finding",
                               "amount": None, "deadline": op, "evidence": [e for e in ev if e],
                               "pickup_src": {"n/a"}, "authority": 1, "fb_used": set(), "decisive": decisive,
                               "kind": "internal", "variant": "EX", "reconciles": True})

    # ==== emitting ====================================================================================================
    def _common(self, cls, base, ev, res):
        return {"class": cls, "invoice_id": base["invoice_id"], "contract_id": base["contract_id"],
                "evidence": _dedupe(ev), "pickup_src": set(res.get("pickup_src") or {"n/a"}),
                "authority": res.get("authority", 1), "fb_used": set(base["fb_used"]),
                "decisive": set(res.get("decisive") or set()), "ocr": base.get("ocr"),
                "lanes": set(res.get("lanes") or set()), "inv_received": base["received"],
                "reconciles": base.get("reconciles"), "inv_doc": base["doc"]}

    def emit_money(self, cls, base, native, c, ev, res, extra=None):
        usd, fx_ev, missing = to_usd(native, base["currency"], c, base["inv_date"])
        it = self._common(cls, base, ev, res)
        it.update(extra or {})
        if missing:
            tre = self.w.contact(c, "treasury") if c else None
            name = "Annex F update" + (f" for {month_key(base['inv_date'])}" if base["inv_date"] else "")
            it.update(verdict="cannot_determine", missing={"name": name, "holder": _holder(c, tre),
                                                            "email": (tre or {}).get("email")},
                      amount=None)
            if c and c.get("fx_note"):
                it["evidence"].append(c["fx_note"])
        else:
            if fx_ev:
                it["evidence"].append(fx_ev)
            it.update(verdict="finding", amount=usd)
            k = norm_invoice(base["invoice_id"])
            self.disputed_native[k] = self.disputed_native.get(k, ZERO) + native
        self.items.append(it)

    def emit_conflict(self, base, c, lo, hi, res):
        lo_u, fx, missing = to_usd(lo, base["currency"], c, base["inv_date"])
        hi_u, _, _ = to_usd(hi, base["currency"], c, base["inv_date"])
        if missing:
            return
        it = self._common("overcharge", base, res["ev"] + ([fx] if fx else []), res)
        it.update(verdict="conflict", amount=None, range=(lo_u, hi_u), variant="XC")
        self.items.append(it)

    def emit_cd(self, cls, base, c, ev, name, holder, email, res):
        it = self._common(cls, base, ev, res)
        it.update(verdict="cannot_determine", amount=None, missing={"name": name, "holder": holder, "email": email},
                  variant="CD")
        self.items.append(it)


def _holder(c, p) -> dict:
    h = {"party": (p or {}).get("party") or (c or {}).get("carrier_party") or "carrier"}
    if p:
        h["person"] = p["name"]
        h["role"] = p.get("title")
    return h


def _received(rec) -> date:
    from .util import parse_date
    return parse_date(rec["received"][:10])


def _dedupe(ev):
    out, seen = [], set()
    for e in ev:
        if not e or not e.get("quote"):
            continue
        k = (e["doc"], e["page"], e["quote"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _guard(fn, what, *args):
    import sys
    import traceback
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - isolate one key's failure, report it on stderr
        print(f"audit: skipped {what}: {e.__class__.__name__}: {e}", file=sys.stderr)
        traceback.print_exc(limit=3, file=sys.stderr)
        return None
