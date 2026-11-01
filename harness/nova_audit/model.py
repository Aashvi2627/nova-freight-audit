"""The fact model for one report: every document received by as_of, read into contracts (with their amendments,
annexes, rate notices and Schedule 1 authority), invoices, credit notes, the payment ledger and shipment records.
Every fact keeps the (doc, page, quote) it came from."""
from __future__ import annotations

import re
import sys
from datetime import date

from . import docs as P
from . import emails as E
from .util import norm_invoice, parse_date, squash

CONFUSABLE = str.maketrans({"1": "I", "0": "O", "5": "S", "8": "B", "l": "I"})


def _received_date(rec) -> date:
    return parse_date(rec["received"][:10])


class World:
    def __init__(self, records: list[dict], as_of: date, state_dir: str | None = None):
        self.state_dir = state_dir
        self.as_of = as_of
        self.recs = [r for r in records if _received_date(r) <= as_of]
        self.rec_by_doc = {r["doc"]: r for r in self.recs}
        self.contracts: dict = {}
        self.amendments: list = []
        self.annexes: list = []
        self.invoices: dict = {}
        self.credit_notes: list = []
        self.registers: list = []
        self.tracking: dict = {}
        self.awbs: dict = {}
        self.masters: dict = {}
        self.dgds: dict = {}
        self.emails: list = []
        self.parts_by_doc: dict = {}
        self._read()

    # ---- reading -----------------------------------------------------------------------------------------------
    def _read(self):
        pending_inv = []
        for rec in self.recs:
            for part in rec["parts"]:
                if not part.get("pages"):
                    continue
                try:
                    x = P.parse_part(part["doc"], part["name"], part["ext"], part["pages"], rec.get("body", ""))
                except Exception as e:  # noqa: BLE001 - an unreadable document is skipped, not fatal
                    print(f"model: could not read {part['doc']}: {e}", file=sys.stderr)
                    continue
                x["received"] = rec["received"]
                x["scan"] = part.get("scan", False)
                x["ocr"] = part.get("ocr")
                x["rec"] = rec
                self.parts_by_doc[part["doc"]] = x
                k = x["kind"]
                if k == "msa" and x.get("contract_id"):
                    self.contracts.setdefault(x["contract_id"], x)
                elif k == "amendment":
                    self.amendments.append(x)
                elif k == "annex":
                    self.annexes.append(x)
                elif k in ("invoice", "credit_note"):
                    pending_inv.append((rec, part, x))
                elif k == "tracking":
                    for e in x["events"]:
                        lst = self.tracking.setdefault(e["awb"], [])
                        if not any(o["event"] == e["event"] and o["date"] == e["date"] for o in lst):
                            lst.append(e)
                elif k == "ap_register":
                    self.registers.append(x)
                elif k == "awb" and x.get("awb"):
                    self.awbs[x["awb"]] = x
                elif k == "master_awb" and x.get("awb"):
                    self.masters[x["awb"]] = x
                elif k == "dgd" and x.get("awb"):
                    self.dgds.setdefault(x["awb"], []).append(x)
        for c in self.contracts.values():
            c["amendments"] = sorted([a for a in self.amendments if a.get("contract_id") == c["contract_id"]],
                                     key=lambda a: (a.get("effective") or date.min, a.get("number") or 0))
            c["annex_docs"] = [a for a in self.annexes if a.get("contract_id") == c["contract_id"]]
            c["carrier_party"] = self._carrier_party(c)
            for p in c["sched1"]:
                p["carrier"] = p["party"] == c["carrier_party"]
            c["domains"] = {p["email"].split("@")[-1]: p["party"] for p in c["sched1"]}
        self.labels = {}
        for c in self.contracts.values():
            self.labels.update(c.get("labels", {}))
        carrier_addrs = {p["email"] for c in self.contracts.values() for p in c["sched1"] if p.get("carrier")}
        for rec in self.recs:
            try:
                f = E.read(rec, self.labels)
            except Exception as e:  # noqa: BLE001
                print(f"model: could not read email {rec['doc']}: {e}", file=sys.stderr)
                f = E.read({**rec, "body": ""}, self.labels)
            if rec["from_addr"] in carrier_addrs:
                try:
                    E.llm_assist(rec, f, self.labels, self.state_dir)
                except Exception as e:  # noqa: BLE001
                    print(f"model: LLM assist failed on {rec['doc']}: {e}", file=sys.stderr)
            self.emails.append(f)
        for rec, part, x in pending_inv:
            try:
                self._add_invoice(rec, part, x)
            except Exception as e:  # noqa: BLE001
                print(f"model: could not index invoice {part['doc']}: {e}", file=sys.stderr)

    def _carrier_party(self, c) -> str | None:
        cid = c.get("contract_id") or ""
        parties = [p["party"] for p in c["sched1"]]
        for p in parties:
            if cid.startswith(p):
                return p
        kam = [p["party"] for p in c["sched1"] if "notices" in p["cats"] and "rate" in "".join(
            q["title"].lower() for q in c["sched1"] if q["party"] == p["party"])]
        sender = c["rec"]["from_addr"]
        for p in c["sched1"]:
            if p["email"] == sender:
                return p["party"]
        return kam[0] if kam else (parties[0] if parties else None)

    def _invoice_id(self, rec, part, x) -> str | None:
        hid = x.get("id")
        subj_ids = re.findall(r"(?:Invoice|Statement|Credit note|Bill|Advice)\s*(?:no\.?)?\s*:?\s*([A-Z][A-Z0-9/\-]*\d)",
                              rec.get("subject", ""), re.I)
        sid = subj_ids[0] if subj_ids else None
        if sid and (not hid or x.get("scan") or hid.translate(CONFUSABLE) == sid.translate(CONFUSABLE)):
            return sid
        if hid:
            return hid
        if sid:
            return sid
        stem = part["name"].rsplit(".", 1)[0]
        return stem if re.search(r"\d", stem) else None

    def _add_invoice(self, rec, part, x):
        iid = self._invoice_id(rec, part, x)
        if not iid:
            return
        x["id"] = iid
        x["key"] = norm_invoice(iid)
        cid = x.get("contract_ref")
        if cid not in self.contracts:
            m = re.search(r"under\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+){2,})", rec.get("body", ""))
            if m and m[1] in self.contracts:
                cid = m[1]
            else:
                cid = next((c for c in self.contracts if c in rec.get("body", "") or c in "\n".join(part["pages"][:1])),
                           cid)
        x["contract_id"] = cid
        if x["kind"] == "credit_note":
            self.credit_notes.append(x)
            return
        ent = self.invoices.get(x["key"])
        if ent is None:
            self.invoices[x["key"]] = {"inv": x, "copies": []}
        else:
            ent["copies"].append({"inv": x, "rec": rec, "facts": next(
                (f for f in self.emails if f["doc"] == rec["doc"]), None)})

    # ---- lookups ------------------------------------------------------------------------------------------------
    def contract(self, cid):
        return self.contracts.get(cid)

    def person(self, c, addr=None, name=None):
        for p in c["sched1"] if c else []:
            if (addr and p["email"] == (addr or "").lower()) or (name and squash(p["name"]) == squash(name)):
                return p
        return None

    def contact(self, c, cat, carrier=True):
        for p in c["sched1"] if c else []:
            if cat in p["cats"] and p["carrier"] == carrier:
                return p
        return None

    def ledger_row(self, inv_key):
        """The latest register row for an invoice (registers in order received)."""
        row = None
        for reg in sorted(self.registers, key=lambda r: r["received"]):
            for r in reg["rows"]:
                if norm_invoice(r["invoice"]) == inv_key:
                    row = r
        return row

    def latest_register(self):
        return max(self.registers, key=lambda r: r["received"]) if self.registers else None

    def events(self, awb):
        ev = list(self.tracking.get(awb, []))
        if not ev and awb and "-H" in awb:
            ev = list(self.tracking.get(awb.split("-H")[0], []))
        return ev

    def pickup(self, awb, line_date=None):
        """(date, source, evidence): tracking PICKED_UP, carrier 'picked up' email, AWB date, invoice date."""
        for e in self.events(awb):
            if e["event"] == "PICKED_UP":
                return e["date"], "tracking", e["ev"]
        for f in self.emails:
            for s in f["status"]:
                if s["awb"] == awb and s["event"] == "PICKED_UP":
                    return s["date"], "email", s["ev"]
        a = self.awbs.get(awb)
        if a and a.get("date"):
            return a["date"], "awb", a.get("ev_date")
        return line_date, "invoice", None

    def release(self, awb):
        evs = self.events(awb)
        arr = next((e for e in sorted(evs, key=lambda e: e["date"]) if e["event"] == "ARRIVED_FACILITY"), None)
        rel = next((e for e in sorted(evs, key=lambda e: e["date"])
                    if e["event"] in ("DELIVERED", "COLLECTED_BY_CONSIGNEE")), None)
        for f in self.emails:
            for s in f["status"]:
                if s["awb"] == awb and s["event"] in ("DELIVERED", "COLLECTED_BY_CONSIGNEE") and rel is None:
                    rel = s
        return arr, rel

    def un_info(self, awb):
        """-> None (unknown) or {'dg': bool other DG present, 'dryice': bool, 'ev': [...]}"""
        ents = []
        for d in self.dgds.get(awb, []):
            ents += d["un"]
        a = self.awbs.get(awb)
        if a:
            ents += a.get("un", [])
        if not ents:
            return None
        return {"dg": any(e["un"] != "1845" for e in ents), "dryice": any(e["un"] == "1845" for e in ents),
                "ev": [e["ev"] for e in ents]}
