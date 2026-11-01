"""Small shared helpers: money, dates, normalisation."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
MONTHS["sept"] = 9


def D(x) -> Decimal | None:
    """Decimal from a printed number ('1,234.56', '1234.56', 1234.56); None when it isn't one."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    s = str(x).strip().replace(",", "").replace("−", "-")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def r2(x: Decimal) -> Decimal:
    """Round half up (ties away from zero) to cents."""
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def money(x) -> str:
    return f"{r2(D(x) or ZERO):.2f}"


def norm_ws(s: str) -> str:
    return " ".join(str(s).split())


def norm_invoice(s: str) -> str:
    """The key form of an invoice id: capitals, no spaces, '#' or ':'."""
    return re.sub(r"[\s#:]", "", str(s or "")).upper()


def squash(s: str) -> str:
    """Lower case, letters and digits only: compares OCR labels that lost their spaces."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# ---- dates ------------------------------------------------------------------------------------------------------------
ISO_RE = r"\d{4}-\d{2}-\d{2}"
SLASH_RE = r"\d{1,2}[/.]\d{1,2}[/.]\d{4}"
TEXT_DMY_RE = r"\d{1,2}\s+(?:[A-Z][a-z]{2,8})\.?\s+\d{4}"
TEXT_MDY_RE = r"(?:[A-Z][a-z]{2,8})\.?\s+\d{1,2},?\s+\d{4}"
ANY_DATE_RE = re.compile(rf"({ISO_RE}|{SLASH_RE}|{TEXT_DMY_RE}|{TEXT_MDY_RE})")


def parse_date(s: str, dayfirst: bool = True) -> date | None:
    """One date in any of the printed forms the mailboxes use."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    t = str(s).strip().rstrip(".,")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:T.*)?", t)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12:
            return _mk(y, b, a)
        if b > 12:
            return _mk(y, a, b)
        return _mk(y, b, a) if dayfirst else _mk(y, a, b)
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", t)
    if m and m[2].lower() in MONTHS:
        return _mk(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", t)
    if m and m[1].lower() in MONTHS:
        return _mk(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    return None


def _mk(y, m, d) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, dayfirst: bool = True) -> list[tuple[date, str]]:
    out = []
    for m in ANY_DATE_RE.finditer(text or ""):
        d = parse_date(m.group(1), dayfirst)
        if d:
            out.append((d, m.group(1)))
    return out


def slash_dayfirst(texts) -> bool:
    """Decide DD/MM against MM/DD for a whole document from any unambiguous slash date in it (default DD/MM)."""
    for t in texts:
        for m in re.finditer(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", t or ""):
            a, b = int(m[1]), int(m[2])
            if a > 12 >= b:
                return True
            if b > 12 >= a:
                return False
    return True


def iso(d) -> str | None:
    return d.isoformat() if d else None


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(s: str) -> str | None:
    """'2035-10', 'October 2035', 'Oct 2035' -> '2035-10'."""
    s = str(s or "").strip()
    m = re.search(r"\b(\d{4})-(\d{2})\b(?!-\d)", s)
    if m and 1 <= int(m[2]) <= 12:
        return f"{m[1]}-{m[2]}"
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b", s)
    if m and m[1].lower() in MONTHS:
        return f"{m[2]}-{MONTHS[m[1].lower()]:02d}"
    return None


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def quarter_of(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarter_bounds(q: str) -> tuple[date, date]:
    y, n = int(q[:4]), int(q[-1])
    start = date(y, 3 * (n - 1) + 1, 1)
    end = add_months(start, 3) - timedelta(days=1)
    return start, end


def day_end_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59)
