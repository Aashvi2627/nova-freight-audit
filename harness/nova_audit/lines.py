"""Layout-independent reading of one billing line.

Every invoice layout we have seen (and the ones we haven't) prints the same facts per line in some order: a waybill,
a lane, a pickup date, a description, a quantity with a unit (or a base and a percentage), a rate and an amount.
Instead of a regex per layout, each line is tokenised by what its pieces *are*, and the rate and amount are told
apart by arithmetic (rate x quantity = amount), with the column position learnt from the document's consistent
lines as the fallback for a line whose arithmetic doesn't hold (which is exactly the line we want to catch)."""
from __future__ import annotations

import re
from decimal import Decimal

from .util import ANY_DATE_RE, D, parse_date, r2

AWB_RE = re.compile(r"(?<![A-Z0-9/\-])([A-Z]{2,4}\d{6,10}(?:-H\d{1,3})?)(?![A-Z0-9])")
LANE_RE = re.compile(r"(?<![A-Z0-9\-])([A-Z]{3}\d{1,2}-[A-Z]{3}\d{1,2})(?![A-Z0-9\-])")
MAWB_RE = re.compile(r"\(\s*MAWB\s+([A-Z0-9\-]+)\s*\)", re.I)
REF_RE = re.compile(r"\(\s*ref\.?\s+([A-Z0-9\-]+)\s*\)", re.I)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
QTY_RE = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s*(kgs?|shpts?|shipments?|days?|fees?|months?|mths?|pcs|pieces|"
                    r"units?|packages?|pkgs?)\b", re.I)
MONTHREF_RE = re.compile(r"(?<![\d-])(\d{4}-\d{2})(?![\d-])")
MONEY_RE = re.compile(r"(?<![\w.,])(-?\d{1,3}(?:,\d{3})+\.\d{2}|-?\d+\.\d{2})(?![\d,]*\d)")
NUM_RE = re.compile(r"(?<![\w.,/])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\w,]*\d)")
LATE_RE = re.compile(r"Late\s*(?:payment\s*)?fee\s*on\s*([A-Z0-9][A-Z0-9/\-]*\d)\s*:?\s*(USD|INR)?\s*([\d,]+\.\d{2})\s*"
                     r"overdue,?\s*month\s*(\d+)\s*@\s*(\d+(?:\.\d+)?)\s*%", re.I)
UNIT_NORM = {"kg": "kg", "kgs": "kg", "shpt": "shpt", "shpts": "shpt", "shipment": "shpt", "shipments": "shpt",
             "day": "day", "days": "day", "fee": "fee", "fees": "fee", "month": "month", "months": "month",
             "mth": "month", "mths": "month", "pcs": "pcs", "pieces": "pcs", "unit": "unit", "units": "unit",
             "package": "pcs", "packages": "pcs", "pkg": "pcs", "pkgs": "pcs"}
NOISE_WORDS = {"usd", "inr", "-", "|", "no", "no."}
TOTAL_RE = re.compile(r"^\s*(?:grand\s*)?total(?![a-z])|^\s*balance\s*due\b|^\s*amount\s*due\b", re.I)
SUBTOTAL_RE = re.compile(r"^\s*(?:sub\s*-?\s*total|net\s*charges|charges)\b", re.I)
TAX_RE = re.compile(r"^\s*(?:tax|gst|igst|cgst|sgst|vat|sales\s*tax)\b", re.I)


def tokens(line: str, dayfirst: bool = True) -> dict:
    """Split one printed line into typed pieces. Never decides rate vs amount (see resolve_line)."""
    s = " " + line.replace("|", " | ") + " "
    out: dict = {"raw": line, "awb": None, "mawb": None, "lane": None, "dates": [], "pct": None, "qty": None,
                 "unit": None, "month_ref": None, "nums": [], "late": None, "desc": ""}
    m = LATE_RE.search(s)
    if m:
        out["late"] = {"ref_invoice": m[1], "currency": (m[2] or "").upper() or None, "principal": D(m[3]),
                       "month": int(m[4]), "pct": D(m[5])}
        s = s[:m.start()] + " Late fee " + s[m.end():]
    m = MAWB_RE.search(s)
    if m:
        out["mawb"] = m[1]
        s = s[:m.start()] + " " + s[m.end():]
    s = REF_RE.sub(" ", s)
    m = AWB_RE.search(s)
    if m:
        out["awb"] = m[1]
        s = s[:m.start()] + " " + s[m.end():]
    m = LANE_RE.search(s)
    if m:
        out["lane"] = m[1]
        s = s[:m.start()] + " " + s[m.end():]
    for dm in list(ANY_DATE_RE.finditer(s))[::-1]:
        d = parse_date(dm.group(1), dayfirst)
        if d:
            out["dates"].insert(0, d)
            s = s[:dm.start()] + " " + s[dm.end():]
    m = MONTHREF_RE.search(s)
    if m:
        out["month_ref"] = m[1]
        s = s[:m.start()] + " " + s[m.end():]
    pcts = list(PCT_RE.finditer(s))
    if pcts:
        out["pct"] = D(pcts[-1][1])
        for pm in pcts[::-1]:
            s = s[:pm.start()] + " " + s[pm.end():]
    m = QTY_RE.search(s)
    if m:
        out["qty"] = D(m[1])
        out["unit"] = UNIT_NORM.get(m[2].lower(), m[2].lower())
        s = s[:m.start()] + " " + s[m.end():]
    s = re.sub(r"^\s*\d{1,3}(?=\s)", " ", s)                 # a leading line number
    s = re.sub(r"(?<![\d.])\d{6}(?![\d.])", " ", s)           # SAC / HSN service codes
    out["nums"] = [D(x) for x in NUM_RE.findall(s)]
    desc = NUM_RE.sub(" ", s)
    words = [w for w in desc.replace("(", " ( ").replace(")", " ) ").split()
             if w.lower() not in NOISE_WORDS and not re.fullmatch(r"[\-|:@]+", w)]
    out["desc"] = re.sub(r"\(\s*\)", "", " ".join(words)).strip(" -:|")
    return out


def has_money(line: str) -> bool:
    return bool(MONEY_RE.search(line))


def resolve_line(t: dict, amount_pos: dict | None = None) -> dict:
    """Decide rate, base and amount from the numbers and their arithmetic."""
    nums = [n for n in t["nums"] if n is not None]
    res = {"amount": None, "rate": None, "base": None, "consistent": False, "amount_index": None}
    if t["late"]:
        pr = t["late"]["principal"]
        # the amount is neither the principal nor a re-print of the fee rate ('1 month 1.50')
        cands = [n for n in nums if n != pr and n != t["late"]["pct"]]
        exp = r2(pr * t["late"]["pct"] / 100)
        hit = next((n for n in cands if abs(n - exp) <= Decimal("0.01")), None)
        res["amount"] = hit if hit is not None else (cands[-1] if cands else None)
        res["rate"] = t["late"]["pct"]
        res["base"] = pr
        res["consistent"] = hit is not None
        return res
    if t["pct"] is not None and len(nums) >= 2:
        for i, b in enumerate(nums):
            for j, a in enumerate(nums):
                if i != j and abs(r2(b * t["pct"] / 100) - a) <= Decimal("0.01"):
                    res.update(base=b, amount=a, rate=t["pct"], consistent=True, amount_index=_pos(j, len(nums)))
                    return res
        pos = (amount_pos or {}).get("pct", "last")
        a = nums[-1] if pos == "last" else nums[0]
        b = nums[0] if pos == "last" else nums[-1]
        res.update(base=b, amount=a, rate=t["pct"])
        return res
    if t["qty"] is not None and nums:
        q = t["qty"]
        if len(nums) == 1:
            res.update(amount=nums[0], rate=r2(nums[0] / q) if q else None)
            return res
        for i, rt in enumerate(nums):
            for j, a in enumerate(nums):
                if i != j and abs(r2(q * rt) - a) <= Decimal("0.01"):
                    res.update(rate=rt, amount=a, consistent=True, amount_index=_pos(j, len(nums)))
                    return res
        pos = (amount_pos or {}).get("qty", "last")
        a = nums[-1] if pos == "last" else nums[0]
        rt = nums[-2] if pos == "last" else nums[1]
        res.update(rate=rt, amount=a)
        return res
    if nums:
        res["amount"] = nums[-1]
    return res


def _pos(j: int, n: int) -> str:
    return "last" if j == n - 1 else ("first" if j == 0 else "mid")
