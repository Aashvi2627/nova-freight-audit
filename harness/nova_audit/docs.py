"""Read documents (pages of text) into structured facts, each with the page and the exact line it came from.

Kinds: msa, amendment, annex (a stand-alone rate annex), invoice (PDF or XLSX, statements included), credit_note,
awb, master_awb, dgd, tracking, ap_register, other. Contracts and amendments are read clause by clause, with the
parameters (free storage days, grace days, late-fee rate, rebate tiers ...) taken from the clause wording."""
from __future__ import annotations

import re
from decimal import Decimal

from . import lines as L
from .util import (ANY_DATE_RE, D, find_dates, parse_date, parse_month, slash_dayfirst, squash)

CONTRACT_ID_RE = re.compile(r"\b([A-Z]{2,5}-[A-Z]{2,3}-[A-Z]{2,5}-[A-Z]?\d{1,4})\b")
PARTY_RE = r"[A-Z][A-Z0-9]{1,5}-[A-Z]{2,3}"
HEADER_NOISE = re.compile(r"^(Confidential\s*\||Initials:|Page \d+ of \d+$)")


class Q:
    """A quotable fact location: (doc, page, quote)."""

    @staticmethod
    def make(doc: str, page: int, quote: str) -> dict:
        return {"doc": doc, "page": page, "quote": " ".join(quote.split())}


# ---- classification ---------------------------------------------------------------------------------------------------
def classify(name: str, ext: str, pages: list[str]) -> str:
    head = "\n".join(p for p in pages[:2])[:3000]
    top = "\n".join(head.splitlines()[:15])
    sq = squash(top)
    if ext == "xlsx":
        first = head.splitlines()[:3]
        h = " ".join(first).lower()
        if "awb" in h and "event" in h:
            return "tracking"
        if "invoice" in h and ("status" in h or "paid" in h):
            return "ap_register"
        return "invoice"
    if "masterservicesagreement" in sq and ("agreementno" in sq or "schedule1" in squash(head)) \
            and "amendment" not in sq[:200]:
        return "msa"
    if re.search(r"Amendment\s+No\.?\s*\d+", top) or "amendmentreference" in sq:
        return "amendment"
    if "masterairwaybill" in sq:
        return "master_awb"
    if "airwaybill" in sq:
        return "awb"
    if "shippersdeclaration" in sq or "dangerousgoodsdeclaration" in sq:
        return "dgd"
    if "creditnote" in sq:
        return "credit_note"
    if re.search(r"invoice|freightbill|chargesadvice|statementofaccount|debitnote|billno|taxinvoice", sq):
        return "invoice"
    if re.search(r"annex[a-z]", sq) and re.search(r"\b\d+(\.\d+)?\s*kg\b", head):
        return "annex"
    return "other"


# ---- contracts --------------------------------------------------------------------------------------------------------
CLAUSE_RE = re.compile(r"^(\d{1,2}\.\d{1,2})\s+([A-Z][A-Za-z ,'()/\-]{1,70}?)\.\s")
HEADING_RE = re.compile(r"^(\d{1,2})\s+[A-Z][A-Za-z ,'()/\-]{2,60}$")
SECTION_RE = re.compile(r"^(Annex [A-Z]\b|Schedule \d+\b|Execution\b|IN WITNESS)")


def body_lines(pages: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, p in enumerate(pages, 1):
        for ln in p.splitlines():
            if ln.strip() and not HEADER_NOISE.match(ln.strip()):
                out.append((i, ln.strip()))
    return out


def clauses(doc: str, pages: list[str]) -> dict:
    """{number: {"title", "text", "quote": evidence on the clause's first page}}"""
    out: dict = {}
    cur = None
    for pg, ln in body_lines(pages):
        m = CLAUSE_RE.match(ln + " ")
        if m:
            cur = {"no": m[1], "title": m[2].strip(), "parts": [(pg, ln)]}
            out.setdefault(m[1], cur)
            continue
        if HEADING_RE.match(ln) or SECTION_RE.match(ln):
            cur = None
            continue
        m = re.match(r"^(\d{1,2})\s+([A-Z][A-Za-z ]{2,40}?)\.\s", ln + " ")   # "12 Notices and Schedule 1. text"
        if m and not re.match(r"^\d{1,2}\s+(kg|days?)", ln):
            cur = {"no": m[1], "title": m[2].strip(), "parts": [(pg, ln)]}
            out.setdefault(m[1], cur)
            continue
        if cur is not None:
            cur["parts"].append((pg, ln))
    res = {}
    for no, c in out.items():
        first = c["parts"][0][0]
        res[no] = {"no": no, "title": c["title"], "text": " ".join(x for _, x in c["parts"]),
                   "ev": Q.make(doc, first, " ".join(x for p, x in c["parts"] if p == first))}
    return res


def find_clause(cls: dict, *title_words: str, text_re: str | None = None) -> dict | None:
    for c in cls.values():
        t = c["title"].lower()
        if all(w in t for w in title_words) and (text_re is None or re.search(text_re, c["text"], re.I)):
            return c
    if text_re:
        for c in cls.values():
            if re.search(text_re, c["text"], re.I):
                return c
    return None


BAND_RE = re.compile(
    r"^(?P<min>minimum(?:\s+charge)?|min\.?(?:\s+charge)?)\b|"
    r"^(?P<lo>\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(?P<hi>\d+(?:\.\d+)?)\s*kg\b|"
    r"^(?P<plus>\d+(?:\.\d+)?)\s*\+\s*kg\b|^(?:over|above|>=?|≥)\s*(?P<over>\d+(?:\.\d+)?)\s*kg\b|"
    r"^(?:under|below|up to|<=?)\s*(?P<under>\d+(?:\.\d+)?)\s*kg\b", re.I)


def rate_row(ln: str) -> dict | None:
    """'USS2-INN1 45-99.5 kg 8.38 8.34' / 'USC3-USS2 100-299.5 kg: 3.12 USD' / 'L minimum charge = USD 85.98'."""
    m = re.match(r"^([A-Z]{2,5}\d{0,2}-[A-Z]{2,5}\d{0,2})\s+(.*)$", ln.strip())
    if not m:
        return None
    lane, rest = m[1], m[2]
    b = BAND_RE.match(rest)
    if not b:
        return None
    tail = rest[b.end():]
    nums = [D(x) for x in re.findall(r"(?<![\w.])\d[\d,]*\.\d+|(?<![\w.])\d+(?![\w.])", tail)]
    nums = [n for n in nums if n is not None]
    if not nums:
        return None
    cur = re.search(r"\b(USD|INR|EUR|GBP)\b", tail)
    row = {"lane": lane, "currency": cur[1] if cur else None, "rates": nums}
    if b["min"]:
        row.update(kind="min", lo=None, hi=None)
    elif b["lo"]:
        row.update(kind="band", lo=D(b["lo"]), hi=D(b["hi"]))
    elif b["plus"] or b["over"]:
        row.update(kind="band", lo=D(b["plus"] or b["over"]), hi=None)
    else:
        row.update(kind="band", lo=Decimal("0"), hi=D(b["under"]))
    return row


def section(bl: list[tuple[int, str]], start_re: str, end_re: str = r"^(Annex [A-Z]\b|Schedule \d+\b)") -> list:
    out, on = [], False
    for pg, ln in bl:
        m = re.match(start_re, ln)
        # a heading ('Annex D: ...', 'Schedule 1 - ...'), not the contents page or a clause that mentions it
        if not on and m and re.match(r"\s*(?:[:\-–]|$)", ln[m.end():]) and not re.search(r"\s\d{1,3}$", ln):
            on = True
            out.append((pg, ln))
            continue
        if on and re.match(end_re, ln):
            break
        if on:
            out.append((pg, ln))
    return out


def parse_contract(doc: str, pages: list[str], email_text: str = "") -> dict:
    bl = body_lines(pages)
    full = "\n".join(ln for _, ln in bl)
    cid = None
    m = re.search(r"Agreement\s+No\.?\s*:?\s*([A-Z0-9][A-Z0-9\-/]+)", full)
    if m:
        cid = m[1]
    if not cid:
        m = CONTRACT_ID_RE.search(full)
        cid = m[1] if m else None
    cls = clauses(doc, pages)
    c: dict = {"kind": "msa", "doc": doc, "contract_id": cid, "clauses": cls, "params": {}, "ev": {}}
    # term
    eff_c = find_clause(cls, "effective")
    eff = None
    if eff_c:
        ds = find_dates(eff_c["text"])
        eff = ds[0][0] if ds else None
        c["ev"]["effective"] = eff_c["ev"]
    if not eff:
        m = re.search(r"(?:takes effect|effective)\s+(?:on|from|as of)\s+(\d{4}-\d{2}-\d{2})", full + "\n" + email_text)
        eff = parse_date(m[1]) if m else None
    c["effective"] = eff
    term_c = find_clause(cls, "term", text_re=r"initial term|term of this agreement")
    end = None
    if term_c:
        ds = find_dates(term_c["text"])
        end = ds[-1][0] if ds else None
        if not end and eff:
            mm = re.search(r"(\d+)\s*months", term_c["text"])
            if mm:
                from .util import add_months
                from datetime import timedelta
                end = add_months(eff, int(mm[1])) - timedelta(days=1)
        c["ev"]["term"] = term_c["ev"]
    c["end"] = end
    ren_c = find_clause(cls, "renew") or find_clause(cls, "continu", text_re=r"initial term")
    auto, ren_months, notice_days, notice_date = False, None, None, None
    if ren_c and ren_c is not term_c:
        t = ren_c["text"]
        c["ev"]["renewal"] = ren_c["ev"]
        if not re.search(r"does not (?:renew|continue)|not renew automatically|no automatic renewal|will not renew|"
                         r"shall not renew", t, re.I) and re.search(r"renew", t, re.I):
            auto = True
            mm = re.search(r"(\d+)[- ]month", t)
            ren_months = int(mm[1]) if mm else 12
            mm = re.search(r"(\d+)\s+days?'?\s*(?:prior\s+)?(?:written\s+)?notice", t, re.I) or \
                re.search(r"notice[^.]*?(?:at least|not less than|no later than)\s+(\d+)\s+days", t, re.I)
            notice_days = int(mm[1]) if mm else None
            ds = find_dates(t)
            notice_date = ds[0][0] if ds and re.search(r"notice[^.]*by|deadline", t, re.I) else None
    c.update(auto_renew=auto, renew_months=ren_months, notice_days=notice_days, notice_date=notice_date)
    c["params"].update(clause_params(cls))
    c["ev"].update(clause_evidence(cls))
    # Annex A
    a = section(bl, r"^Annex A\b")
    c["periods"], c["rates"], c["currency"] = [], [], None
    for pg, ln in a:
        m = re.search(r"\((USD|INR|EUR|GBP) per", ln)
        if m and not c["currency"]:
            c["currency"] = m[1]
        m = re.match(r"^Period\s+(\S+?):?\s+pickups\s+(\S+)\s+to\s+(\S+)", ln, re.I)
        if m:
            c["periods"].append({"name": m[1].rstrip(":"), "start": parse_date(m[2]), "end": parse_date(m[3])})
            continue
        r = rate_row(ln)
        if r:
            r["ev"] = Q.make(doc, pg, ln)
            c["rates"].append(r)
    if not c["currency"]:
        m = re.search(r"\b(USD|INR)\b", full)
        c["currency"] = m[1] if m else "USD"
    # Annex C fuel
    c["fuel"] = {}
    for pg, ln in section(bl, r"^Annex C\b"):
        m = re.match(r"^(\d{4}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{4})\s+(\d+(?:\.\d+)?)\s*%", ln)
        if m and parse_month(m[1]):
            c["fuel"][parse_month(m[1])] = {"pct": D(m[2]), "ev": Q.make(doc, pg, ln)}
    # Annex D accessorials (with wrapped continuation lines)
    c["acc"] = {}
    last = None
    for pg, ln in section(bl, r"^Annex D\b", r"^(Annex [A-CE-Z]\b|Schedule \d+\b)"):
        m = re.match(r"^([A-Z][A-Z0-9_]{1,11})\s+(.+?)\s+([\d,]+\.\d{2})\s+(USD|INR|EUR|GBP)\s+per\s+(.+)$", ln)
        if m:
            last = m[1]
            c["acc"][m[1]] = {"name": m[2], "fee": D(m[3]), "currency": m[4], "basis": m[5], "ev": Q.make(doc, pg, ln)}
        elif last and not re.match(r"^(Code|GST|Annex|Schedule)\b", ln) and ln[:1].islower() or \
                (last and re.match(r"^[A-Z][a-z]+ to [A-Z][a-z]+", ln)):
            c["acc"][last]["basis"] += " " + ln
    for code, a_ in c["acc"].items():
        a_["months"] = months_in(a_["basis"])
    # Annex E labels
    c["labels"] = {}
    c["code_names"] = {}
    buf = []
    for pg, ln in section(bl, r"^Annex E\b", r"^(Annex [A-DF-Z]\b|Schedule \d+\b)"):
        if ln.startswith("Code "):
            buf.append([pg, ln])
        elif buf and not ln.startswith("Annex E"):
            buf[-1][1] += " " + ln
    c["label_ev"] = {}
    for pg, ln in buf:
        m = re.match(r"^Code\s+([A-Z0-9_]+):\s*([^;]+?)\s*(?:;\s*also billed as:\s*(.+))?$", ln)
        if not m:
            continue
        code = m[1]
        labels = [m[2]] + ([x.strip() for x in re.split(r",\s*", m[3])] if m[3] else [])
        c["code_names"][code] = m[2].strip()
        for lb in labels:
            c["labels"][lb.strip().lower()] = code
        c["label_ev"][code] = Q.make(doc, pg, ln)
    # Annex F
    c["fx"] = {}
    c["fx_note"] = None
    for pg, ln in section(bl, r"^Annex F\b"):
        m = re.match(r"^(\d{4}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{4})\s+(\d+(?:\.\d+)?)\s*$", ln)
        if m and parse_month(m[1]):
            c["fx"][parse_month(m[1])] = {"rate": D(m[2]), "ev": Q.make(doc, pg, ln)}
        elif re.search(r"update|issued by", ln, re.I):
            c["fx_note"] = Q.make(doc, pg, ln)
    c["fx_applicable"] = not any(re.search(r"not applicable", ln, re.I) for _, ln in section(bl, r"^Annex F\b")[:3])
    # Schedule 1
    c["sched1"] = []
    for pg, ln in section(bl, r"^Schedule 1\b", r"^(Annex [A-Z]\b|Schedule [2-9]\b|Initials)"):
        m = re.match(rf"^(.+?),\s*(.+?),\s*({PARTY_RE})\s*:\s*(.+?)\s*<([^>]+@[^>]+)>", ln)
        if m:
            cats = [x.strip().lower().replace(" ", "_") for x in re.split(r",|\band\b", m[4]) if x.strip()]
            c["sched1"].append({"name": m[1].strip(), "title": m[2].strip(), "party": m[3], "cats": cats,
                                "email": m[5].strip().lower(), "ev": Q.make(doc, pg, ln)})
    return c


def months_in(text: str) -> set | None:
    """'for pickups in September to November only' -> {9, 10, 11}."""
    from .util import MONTHS
    m = re.search(r"([A-Z][a-z]+)\s+(?:to|-|through|until)\s+([A-Z][a-z]+)", text)
    if m and m[1].lower() in MONTHS and m[2].lower() in MONTHS:
        a, b = MONTHS[m[1].lower()], MONTHS[m[2].lower()]
        return set(range(a, b + 1)) if a <= b else set(range(a, 13)) | set(range(1, b + 1))
    ms = [MONTHS[w.lower()] for w in re.findall(r"\b([A-Z][a-z]+)\b", text) if w.lower() in MONTHS
          and len(w) > 3]
    return set(ms) or None


def clause_params(cls: dict) -> dict:
    p: dict = {}
    c = find_clause(cls, "storage", text_re=r"free days")
    if c:
        m = re.search(r"(\d+)\s+free\s+days", c["text"])
        p["free_days"] = int(m[1]) if m else None
    c = find_clause(cls, "payment terms") or find_clause(cls, "payment", text_re=r"due\s+\d+\s+days|\d+\s+days")
    if c:
        m = re.search(r"(\d+)\s+days", c["text"])
        p["payment_days"] = int(m[1]) if m else None
    c = find_clause(cls, "late fee") or find_clause(cls, "late", text_re=r"late fee")
    if c:
        t = c["text"]
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*per\s*month", t) or re.search(r"(\d+(?:\.\d+)?)\s*%", t)
        p["late_pct"] = D(m[1]) if m else None
        m = re.search(r"days_late\s*<=\s*(\d+)", t) or re.search(r"more than\s+(\d+)\s+days?\s+(?:after|past)", t) or \
            re.search(r"grace(?: period)? of\s+(\d+)\s+days", t)
        p["grace_days"] = int(m[1]) if m else 0
    c = find_clause(cls, "compound") or find_clause(cls, "late", text_re=r"compound|simple interest|principal only")
    comp_text = (c or {}).get("text", "")
    if re.search(r"not compound|does not compound|simple|principal only|no compounding", comp_text, re.I):
        p["compound"] = False
    elif re.search(r"added to the overdue balance|principal plus unpaid|compound", comp_text, re.I):
        p["compound"] = True
    else:
        p["compound"] = False
    c = find_clause(cls, "volume rebate") or find_clause(cls, "rebate", text_re=r"tier|QS\s*>=")
    p["rebate_tiers"] = []
    if c and not re.search(r"not applicable", c["text"], re.I):
        for thr, pct in re.findall(r"(?:>=|≥|at least|of)\s*(?:USD|US\$|\$|INR)?\s*([\d,]+(?:\.\d+)?)\s*:?\s*"
                                   r"(?:[^%;:]{0,30}?)(\d+(?:\.\d+)?)\s*%", c["text"]):
            if D(thr) and D(thr) > 100:
                p["rebate_tiers"].append((D(thr), D(pct)))
    c = find_clause(cls, "rebate credit") or find_clause(cls, "rebate", text_re=r"credit notes?.*days")
    if c:
        m = re.search(r"(\d+)\s+days", c["text"])
        p["rebate_due_days"] = int(m[1]) if m else None
    c = find_clause(cls, "improvement")
    if c and not re.search(r"not applicable", c["text"], re.I):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", c["text"])
        p["improvement_pct"] = D(m[1]) if m else None
        m = re.search(r"(\d+)\s+days", c["text"])
        p["improvement_due_days"] = int(m[1]) if m else None
    c1 = find_clause(cls, "handling margin")
    c2 = find_clause(cls, "step-down") or find_clause(cls, "step down")
    if c1 and not re.search(r"not applicable", c1["text"], re.I):
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", c1["text"])
        p["margin_pct"] = D(m[1]) if m else None
    if c2 and not re.search(r"not applicable", c2["text"], re.I):
        t = c2["text"]
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", t)
        thr = re.search(r"(?:USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)", t)
        p["margin_step_pct"] = D(pcts[-1]) if pcts else None
        p["margin_step_threshold"] = D(thr[1]) if thr else None
        p["margin_step_text"] = t
    alltext = " ".join(x["text"] for x in cls.values())
    p["unranked_notice_vs_amendment"] = bool(re.search(r"neither prevails|resolve the difference jointly", alltext,
                                                       re.I))
    p["notice_prevails"] = bool(re.search(r"rate notices? (?:shall )?prevails?", alltext, re.I))
    return p


def clause_evidence(cls: dict) -> dict:
    ev = {}
    for key, args in {"pricing": (("pricing",), None), "chargeable": (("chargeable",), None),
                      "minimum": (("minimum",), None), "notices": (("rate notice",), None),
                      "fuel": (("fuel",), None), "dg": (("dangerous",), None), "dryice": (("dry ice",), None),
                      "mixed": (("mixed",), None), "storage": (("storage",), r"free days"),
                      "delivery": (("delivery",), None), "customs": (("customs",), None),
                      "rebate": (("volume rebate",), None), "rebate_credit": (("rebate credit",), None),
                      "improvement": (("improvement",), None), "qs": (("qualifying",), None),
                      "late": (("late fee",), None), "compound": (("compound",), None),
                      "extension": (("extension of due",), None), "waiver": (("waiver authority",), None),
                      "waiver_scope": (("scope of waiver",), None), "conditional": (("conditional",), None),
                      "unranked": (("unranked",), None), "precedence": (("precedence",), None),
                      "margin": (("handling margin",), None), "step": (("step",), None),
                      "payment": (("payment terms",), None)}.items():
        c = find_clause(cls, *args[0], text_re=args[1])
        if c:
            ev[key] = c["ev"]
    return ev


def parse_amendment(doc: str, pages: list[str]) -> dict:
    bl = body_lines(pages)
    full = "\n".join(ln for _, ln in bl)
    a: dict = {"kind": "amendment", "doc": doc, "rates": [], "annex_refs": [], "ev": {}}
    m = re.search(r"Amendment\s+reference\s+(\S+)", full)
    a["ref"] = m[1] if m else None
    m = re.search(r"Amendment\s+No\.?\s*(\d+)", full)
    a["number"] = int(m[1]) if m else None
    m = CONTRACT_ID_RE.search(a["ref"] or "") or CONTRACT_ID_RE.search(full)
    a["contract_id"] = m[1] if m else None
    a["effective"] = None
    for pg, ln in bl:
        m = re.search(r"Effective\s+(?:from|date|as of|on)\s*:?\s*(.+)$", ln, re.I)
        if m:
            ds = find_dates(m[1])
            if ds:
                a["effective"] = ds[0][0]
                a["ev"]["effective"] = Q.make(doc, pg, ln)
                break
    sig = len(re.findall(r"Signed for and on behalf|Signature", full))
    a["signed"] = sig >= 2 and bool(re.search(r"signed", full, re.I))
    exec_dates = [parse_date(x) for x in re.findall(r"^Date:\s*(\S+)", full, re.M)]
    a["signed_on"] = max([d for d in exec_dates if d], default=None)
    for pg, ln in bl:
        r = rate_row(ln)
        if r:
            r["ev"] = Q.make(doc, pg, ln)
            a["rates"].append(r)
    for i, (pg, ln) in enumerate(bl):
        if re.search(r"\((?:attached|enclosed)\)", ln, re.I):
            sent = ln
            j = i + 1
            while j < len(bl) and bl[j][0] == pg and not re.match(r"^\d+\.\s|^All other|^This Amendment", bl[j][1]):
                sent += " " + bl[j][1]
                j += 1
            m = re.search(r"((?:Annex|Schedule|Appendix|Rate (?:card|schedule))[\w .\-]*?)\s*\((?:attached|enclosed)\)",
                          sent, re.I)
            if not m:
                continue
            name = re.sub(r"^.*\b(?=(Annex|Schedule|Appendix|Rate)\b)", "", m[1]).strip()
            lanes = L.LANE_RE.findall(sent)
            ds = find_dates(sent)
            a["annex_refs"].append({"name": name, "lanes": lanes, "from": ds[0][0] if ds else a["effective"],
                                    "ev": Q.make(doc, pg, sent)})
    m = re.search(r"(?:term|Agreement)[^.]{0,80}?(?:extended|continues?|renewed)[^.]{0,60}?(?:until|to|through|"
                  r"ending on|expires? on|expiring on)\s+(\d{4}-\d{2}-\d{2})", full, re.I)
    if m:
        a["term_end"] = parse_date(m[1])
        for pg, ln in bl:
            if m[1] in ln:
                a["ev"]["term"] = Q.make(doc, pg, ln)
                break
    a["clauses"] = clauses(doc, pages)
    a["params"] = {k: v for k, v in clause_params(a["clauses"]).items()
                   if v not in (None, [], False, 0) and k not in ("compound",)}
    return a


def parse_annex(doc: str, pages: list[str], name: str) -> dict:
    bl = body_lines(pages)
    full = "\n".join(ln for _, ln in bl)
    x = {"kind": "annex", "doc": doc, "rates": [], "periods": []}
    m = CONTRACT_ID_RE.search(full)
    x["contract_id"] = m[1] if m else None
    m = re.search(r"(Annex\s+[A-Z](?:\s+rev(?:ision)?\.?\s*\d+)?)", full, re.I)
    x["name"] = m[1] if m else name
    ds = find_dates(" ".join(ln for _, ln in bl if re.search(r"appl|effective|from", ln, re.I)))
    x["from"] = ds[0][0] if ds else None
    for pg, ln in bl:
        m = re.match(r"^Period\s+(\S+?):?\s+pickups\s+(\S+)\s+to\s+(\S+)", ln, re.I)
        if m:
            x["periods"].append({"name": m[1], "start": parse_date(m[2]), "end": parse_date(m[3])})
        r = rate_row(ln)
        if r:
            r["ev"] = Q.make(doc, pg, ln)
            x["rates"].append(r)
    return x


# ---- invoices and credit notes ---------------------------------------------------------------------------------------
ID_PATTERNS = [
    r"(?:INVOICE|Invoice\s*(?:no\.?|number|#))\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"Freight\s*bill\s*(?:no\.?|number)?\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"Charges\s*advice\s*(?:no\.?)?\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"STATEMENT\s*OF\s*ACCOUNT\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"Statement\s*(?:no\.?|number)\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"CREDIT\s*NOTE\s*(?:no\.?)?\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"Credit\s*note\s*(?:no\.?|number)\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
    r"(?:Debit\s*note|Bill|Document)\s*(?:no\.?|number)\s*[:#]?\s*([A-Z][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
]
DATE_LABELS = [("issue", r"(?:invoice|document|statement|credit|bill|issue)\s*date|issued\s*on|date\s*of\s*issue|"
                         r"dated"),
               ("due", r"due\s*date|payable\s*by|settle\s*by|pay(?:ment)?\s*by|payment\s*due|due\s*on|due\s*by")]


def header_id(text_lines: list[str]) -> str | None:
    for ln in text_lines[:25]:
        for pat in ID_PATTERNS:
            m = re.search(pat, ln)
            if m and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m[1]):
                return m[1].rstrip(".,")
    return None


def header_dates(text_lines: list[str], dayfirst: bool) -> dict:
    out: dict = {}
    for ln in text_lines[:30]:
        prev = 0
        for dm in ANY_DATE_RE.finditer(ln):
            d = parse_date(dm.group(1), dayfirst)
            label = ln[prev:dm.start()]
            prev = dm.end()
            if not d:
                continue
            for k, pat in DATE_LABELS:
                if k not in out and re.search(pat + r"\W*$", label, re.I):
                    out[k] = (d, ln)
    return out


def parse_invoice(doc: str, pages: list[str], ext: str, kind: str = "invoice") -> dict:
    texts = [p.splitlines() for p in pages]
    all_lines = [ln for t in texts for ln in t]
    dayfirst = slash_dayfirst(pages)
    inv: dict = {"kind": kind, "doc": doc, "ext": ext, "id": header_id(all_lines), "lines": [], "dayfirst": dayfirst}
    hd = header_dates(all_lines, dayfirst)
    inv["date"] = hd.get("issue", (None, None))[0]
    inv["due"] = hd.get("due", (None, None))[0]
    inv["ev_header"] = Q.make(doc, 1, hd["issue"][1]) if "issue" in hd else None
    inv["ev_due"] = Q.make(doc, 1, hd["due"][1]) if "due" in hd else None
    head = "\n".join(all_lines[:30])
    m = re.search(r"(?:Contract(?:\s*reference)?|Service\s*agreement|Agreement|Contract\s*ref\.?)\s*:?\s*"
                  r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+){2,})", head, re.I)
    inv["contract_ref"] = m[1] if m else (CONTRACT_ID_RE.search(head)[1] if CONTRACT_ID_RE.search(head) else None)
    cur = re.search(r"Currency\s*:?\s*(USD|INR|EUR|GBP)|Amount\s*\((USD|INR)\)|Net\s*\((USD|INR)\)", head, re.I)
    inv["currency"] = next((g.upper() for g in (cur.groups() if cur else []) if g), None)
    inv["copy_marked"] = bool(re.search(r"\b(COPY|DUPLICATE COPY|for your records|NOT AN INVOICE)\b", head))
    # lines
    in_footer = False
    raw_lines = []
    for pi, t in enumerate(texts, 1):
        in_footer = False
        for ln in t:
            s = re.sub(r"\b(USD|INR|EUR|GBP)(?=\d)", r"\1 ", ln.strip())     # OCR glues 'INR108485.85'
            if not s:
                continue
            if L.TOTAL_RE.match(s):
                nums = [D(x) for x in L.MONEY_RE.findall(s)]
                if nums:
                    inv["total"] = nums[-1]
                    inv["ev_total"] = Q.make(doc, pi, s)
                c = re.search(r"\b(USD|INR|EUR|GBP)\b", s)
                if c and not inv["currency"]:
                    inv["currency"] = c[1]
                in_footer = True
                continue
            if L.SUBTOTAL_RE.match(s) and L.has_money(s):
                nums = [D(x) for x in L.MONEY_RE.findall(s)]
                inv["subtotal"] = nums[-1] if nums else None
                c = re.search(r"\b(USD|INR|EUR|GBP)\b", s)
                if c and not inv["currency"]:
                    inv["currency"] = c[1]
                in_footer = True
                continue
            if L.TAX_RE.match(s) and L.has_money(s):
                nums = [D(x) for x in L.MONEY_RE.findall(s)]
                raw_lines.append((pi, s, {"tax": nums[-1] if nums else None}))
                continue
            if in_footer or not L.has_money(s):
                continue
            if re.match(r"^(Payment terms|Terms|Remit|Settlement|Please|Bank|GSTIN|Invoice date|Document date|"
                        r"Issued|Statement date|Credit date)", s, re.I):
                continue
            raw_lines.append((pi, s, None))
    toks = []
    for pi, s, tax in raw_lines:
        if tax is not None:
            toks.append((pi, s, None, tax))
            continue
        t = L.tokens(s, dayfirst)
        if not (t["awb"] or t["lane"] or t["late"] or t["month_ref"] or t["qty"] is not None or t["pct"] is not None):
            continue
        toks.append((pi, s, t, None))
    # learn where the amount sits from the lines whose arithmetic holds
    pos_votes: dict = {"pct": {}, "qty": {}}
    for _, _, t, _ in toks:
        if t is None:
            continue
        r = L.resolve_line(t)
        if r["consistent"] and r["amount_index"]:
            k = "pct" if t["pct"] is not None else "qty"
            pos_votes[k][r["amount_index"]] = pos_votes[k].get(r["amount_index"], 0) + 1
    amount_pos = {k: max(v, key=v.get) if v else "last" for k, v in pos_votes.items()}
    for n, (pi, s, t, tax) in enumerate(toks):
        if t is None:
            inv["lines"].append({"n": n, "page": pi, "raw": s, "desc": "Tax", "tax": True, "amount": tax.get("tax"),
                                 "ev": Q.make(doc, pi, s)})
            continue
        r = L.resolve_line(t, amount_pos)
        inv["lines"].append({
            "n": n, "page": pi, "raw": s, "ev": Q.make(doc, pi, s), "awb": t["awb"], "mawb": t["mawb"],
            "lane": t["lane"], "date": t["dates"][0] if t["dates"] else None, "desc": t["desc"], "qty": t["qty"],
            "unit": t["unit"], "pct": t["pct"], "month_ref": t["month_ref"], "late": t["late"],
            "amount": r["amount"], "rate": r["rate"], "base": r["base"], "consistent": r["consistent"]})
    if not inv["currency"]:
        m = re.search(r"\b(USD|INR)\b", "\n".join(all_lines))
        inv["currency"] = m[1] if m else "USD"
    tot = sum((ln["amount"] or 0) for ln in inv["lines"])
    target = inv.get("total") if inv.get("total") is not None else inv.get("subtotal")
    inv["reconciles"] = target is not None and abs(tot - target) <= Decimal("0.05")
    if kind == "credit_note":
        parse_credit_extras(inv, all_lines)
    return inv


def parse_credit_extras(cn: dict, all_lines: list[str]) -> None:
    text = "\n".join(all_lines)
    m = re.search(r"Credit\s*type\s*:?\s*([A-Za-z_ \-]+?)\s*(?:\||$|period)", text, re.I | re.M)
    ctype = (m[1].strip().lower() if m else "")
    if not ctype:
        if re.search(r"improvement", text, re.I):
            ctype = "improvement"
        elif re.search(r"rebate", text, re.I):
            ctype = "rebate"
        elif re.search(r"margin", text, re.I):
            ctype = "margin"
    cn["credit_type"] = ctype
    m = re.search(r"period\s*:?\s*(\d{4}-Q[1-4]|CY\d+)", text, re.I)
    cn["period"] = m[1].upper() if m else None
    if not cn["date"]:
        ds = find_dates(text)
        cn["date"] = ds[0][0] if ds else None
    m = re.search(r"Total\s*credit\s*:?\s*(USD|INR)?\s*([\d,]+\.\d{2})", text, re.I)
    if m:
        cn["total"] = D(m[2])
        cn["total_currency"] = m[1]
    refs = re.findall(r"(?:invoice|inv\.?|against|re:?)\s*(?:no\.?)?\s*([A-Z][A-Z0-9]*[/\-][A-Z0-9/\-]*\d)", text, re.I)
    cn["ref_invoices"] = [r for r in refs if not CONTRACT_ID_RE.fullmatch(r) and not re.match(r"\d{4}-", r)]
    cn["awbs"] = sorted(set(L.AWB_RE.findall(text)))


# ---- shipment records -------------------------------------------------------------------------------------------------
def parse_awb(doc: str, pages: list[str], kind: str) -> dict:
    bl = body_lines(pages)
    full = "\n".join(ln for _, ln in bl)
    a: dict = {"kind": kind, "doc": doc}
    m = re.search(r"AIR\s*WAYBILL\s+([A-Z0-9\-]+)", full, re.I)
    a["awb"] = m[1] if m else None
    m = re.search(r"Master\s+AWB\s+([A-Z0-9\-]+)", full, re.I)
    a["master"] = m[1] if m else None
    m = re.search(r"Executed on\s+(\S+)(?:\s+at\s+(\S+))?", full)
    a["date"] = parse_date(m[1]) if m else None
    a["ev_date"] = next((Q.make(doc, pg, ln) for pg, ln in bl if ln.startswith("Executed")), None)
    m = re.search(r"Origin\s+(\S+)\s*\|\s*Destination\s+(\S+)", full)
    a["origin"], a["dest"] = (m[1], m[2]) if m else (None, None)
    m = re.search(r"Chargeable weight\s+([\d.,]+)\s*kg", full)
    a["chargeable"] = D(m[1]) if m else None
    a["ev_weight"] = next((Q.make(doc, pg, ln) for pg, ln in bl if ln.startswith("Chargeable weight")), None)
    m = re.search(r"Actual weight\s+([\d.,]+)\s*kg", full)
    a["actual"] = D(m[1]) if m else None
    m = re.search(r"Dimensions\s+(\d+)\s*x\s*(\d+)\s*x\s*(\d+)\s*cm\s*x\s*(\d+)", full)
    a["dims"] = [int(x) for x in m.groups()] if m else None
    m = re.search(r"Total chargeable weight\s+([\d.,]+)\s*kg", full)
    a["total_chargeable"] = D(m[1]) if m else None
    a["ev_total"] = next((Q.make(doc, pg, ln) for pg, ln in bl if "Total chargeable" in ln), None)
    a["houses"] = {h: D(w) for h, w in re.findall(r"House\s+([A-Z0-9\-]+):\s*\d+\s*pieces?,\s*([\d.]+)\s*kg", full)}
    a["un"] = un_entries(doc, bl)
    a["goods"] = (re.search(r"Nature of goods:\s*(.+)", full) or [None, ""])[1]
    return a


def un_entries(doc: str, bl) -> list[dict]:
    out = []
    for pg, ln in bl:
        for m in re.finditer(r"\bUN\s?(\d{4})\b", ln):
            out.append({"un": m[1], "refrigerant": bool(re.search(r"refrigerant", ln, re.I)),
                        "only": bool(re.search(r"refrigerant only|solely as a refrigerant", ln, re.I)),
                        "ev": Q.make(doc, pg, ln)})
    return out


def parse_dgd(doc: str, pages: list[str]) -> dict:
    bl = body_lines(pages)
    full = "\n".join(ln for _, ln in bl)
    m = re.search(r"(?:AWB|air waybill)\s+([A-Z]{2,4}\d{6,10}(?:-H\d+)?)", full, re.I)
    return {"kind": "dgd", "doc": doc, "awb": m[1] if m else None, "un": un_entries(doc, bl)}


def xlsx_rows(page: str) -> list[list[str]]:
    return [r.split(" | ") for r in page.splitlines() if r.strip()]


def _col(header: list[str], *names) -> int | None:
    for i, h in enumerate(header):
        hl = h.lower()
        if any(n in hl for n in names):
            return i
    return None


def parse_tracking(doc: str, pages: list[str]) -> dict:
    ev = []
    for pi, page in enumerate(pages, 1):
        rows = xlsx_rows(page)
        if not rows:
            continue
        hdr = rows[0]
        ia, ie, idt = _col(hdr, "awb", "waybill", "shipment"), _col(hdr, "event", "status"), _col(hdr, "date")
        ist, ir = _col(hdr, "station", "location"), _col(hdr, "reason")
        for row in rows[1:]:
            line = " | ".join(row)
            if ia is None or ie is None or idt is None or len(row) <= max(ia, ie, idt):
                continue
            d = parse_date(row[idt])
            if not d:
                continue
            ev.append({"awb": row[ia].strip(), "event": row[ie].strip().upper(), "date": d,
                       "station": row[ist].strip() if ist is not None and ist < len(row) else None,
                       "reason": row[ir].strip() if ir is not None and ir < len(row) else None,
                       "ev": Q.make(doc, pi, line)})
    return {"kind": "tracking", "doc": doc, "events": ev}


def parse_register(doc: str, pages: list[str]) -> dict:
    out = []
    for pi, page in enumerate(pages, 1):
        rows = xlsx_rows(page)
        if not rows:
            continue
        hdr = rows[0]
        ii, idt, idue = _col(hdr, "invoice no", "invoice", "document"), _col(hdr, "invoice date", "date"), \
            _col(hdr, "due")
        iam, ist, ipd = _col(hdr, "amount", "total"), _col(hdr, "status"), _col(hdr, "paid")
        if idt == ii:
            idt = next((i for i, h in enumerate(hdr) if "date" in h.lower() and "due" not in h.lower()
                        and "paid" not in h.lower()), None)
        for row in rows[1:]:
            line = " | ".join(row)
            g = lambda i: row[i].strip() if i is not None and i < len(row) else ""  # noqa: E731
            if not g(ii):
                continue
            amt = g(iam)
            cur = re.search(r"(USD|INR|EUR|GBP)", amt)
            out.append({"invoice": g(ii), "date": parse_date(re.sub(r"^\D+", "", g(idt))),
                        "due": parse_date(re.sub(r"^\D+", "", g(idue))), "amount": D(re.sub(r"[^\d.,\-]", "", amt)),
                        "currency": cur[1] if cur else None, "status": g(ist).lower(),
                        "paid": parse_date(re.sub(r"^\D+", "", g(ipd))) if ipd is not None else None,
                        "ev": Q.make(doc, pi, line)})
    return {"kind": "ap_register", "doc": doc, "rows": out}


def parse_part(doc: str, name: str, ext: str, pages: list[str], email_text: str = "") -> dict:
    kind = classify(name, ext, pages)
    if kind == "msa":
        return parse_contract(doc, pages, email_text)
    if kind == "amendment":
        return parse_amendment(doc, pages)
    if kind == "annex":
        return parse_annex(doc, pages, name)
    if kind in ("invoice", "credit_note"):
        return parse_invoice(doc, pages, ext, kind)
    if kind in ("awb", "master_awb"):
        return parse_awb(doc, pages, kind)
    if kind == "dgd":
        return parse_dgd(doc, pages)
    if kind == "tracking":
        return parse_tracking(doc, pages)
    if kind == "ap_register":
        return parse_register(doc, pages)
    return {"kind": "other", "doc": doc}
