"""Flex Bucks (Atrium) transaction scraper — plain HTTP, no browser.

Atrium's guest login is an ordinary form post: GET the login page to get a
session key (skey, also the jsa_session cookie), POST the guest email/password
with that skey, and the skey becomes an authenticated session. Statement pages
then take the skey as a query parameter. Session keys are bound to the client
IP (see ben.page/wrapped), so the login and statement requests must come from
the same machine — they do, both run here.
"""
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

LOGIN_URL = "https://studentlink.atriumcampus.com/login.php?guest=1&cid=410"
STATEMENT_BASE = "https://connectobu.atriumcampus.com/statementdetail.php"
CID = "410"
TIMEOUT = 30  # seconds per request
USER_AGENT = "Mozilla/5.0 (compatible; OBU-Stu-Wrapped/2.0; +https://wrapped.drew.place)"


class LoginFailed(Exception):
    """Atrium rejected the guest email/password."""


def _skey_from_url(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("skey", [])
    return values[0] if values and values[0] else None


def _login_form(html: str):
    return BeautifulSoup(html, "lxml").find("input", attrs={"name": "loginphrase"})


def login(session: requests.Session, username: str, password: str) -> str:
    """Log in as a Flex Bucks guest and return the authenticated skey."""
    page = session.get(LOGIN_URL, timeout=TIMEOUT)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "lxml")
    field = soup.find("input", attrs={"name": "loginphrase"})
    form = field.find_parent("form") if field else None
    if form is None:
        raise RuntimeError("Atrium login page changed: no login form found")

    fields = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}
    fields["loginphrase"] = username
    fields["password"] = password
    # The form is multipart/form-data; files=(None, value) sends plain fields that way.
    resp = session.post(
        form.get("action") or page.url,
        files={k: (None, v) for k, v in fields.items()},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    if _login_form(resp.text) is not None:
        raise LoginFailed("Atrium did not accept the guest login")

    skey = _skey_from_url(resp.url) or fields.get("skey")
    if not skey:
        raise RuntimeError("Atrium login succeeded but no session key was found")
    return skey


def build_statement_url(skey, startdate, enddate, acct="1"):
    params = {"cid": CID, "skey": skey, "startdate": startdate, "enddate": enddate, "acct": acct}
    return f"{STATEMENT_BASE}?{urlencode(params)}"


def fetch_transactions(username, password, startdate, enddate, acct="1", progress=lambda step, msg: None):
    """Log in and return parsed transactions between startdate and enddate (ISO dates)."""
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        progress(2, "Logging in to Flex Bucks...")
        skey = login(session, username, password)

        progress(3, "Loading your transaction history...")
        resp = session.get(build_statement_url(skey, startdate, enddate, acct), timeout=TIMEOUT)
        resp.raise_for_status()
        if _login_form(resp.text) is not None:
            raise RuntimeError("Atrium sent the statement request back to the login page")

    progress(4, "Parsing your food data...")
    return parse_transactions(resp.text)


def _safe_float(td):
    if td is None:
        return None
    try:
        return float(td.get_text(strip=True))
    except (ValueError, TypeError):
        return None


def parse_transactions(html):
    """
    Parse full transaction data. Returns list of dicts:
      {date, location, transaction_id, amount, balance_after, grand_total,
       items: [{name, modifier, unit_price, qty, promo, subtotal}]}
    Only transactions with at least one line item are included.
    """
    soup = BeautifulSoup(html, "lxml")
    transactions = []
    date_cells = soup.find_all("th", class_="jsa_month")

    for date_cell in date_cells:
        outer_row = date_cell.parent
        date_str = date_cell.get_text(" ", strip=True).strip("\xa0").strip()

        desc_td = outer_row.find("td", class_="jsa_desc")
        if not desc_td:
            continue
        details_el = desc_td.find("details")
        if not details_el:
            continue

        summary = details_el.find("summary")
        location = summary.get_text(strip=True) if summary else ""

        region = details_el.find("div", attrs={"role": "region"})
        transaction_id = ""
        if region:
            for div in region.find_all("div"):
                text = div.get_text(strip=True)
                if "Transaction ID:" in text:
                    transaction_id = text.replace("Transaction ID:", "").strip()
                    break

        amount_td = outer_row.find("td", class_="jsa_amount")
        amount = 0.0
        if amount_td:
            try:
                tok = amount_td.get_text(" ", strip=True).split()[0]
                amount = float(tok)
            except (ValueError, IndexError):
                pass

        balance_td = outer_row.find("td", class_="jsa_balance")
        balance_after = _safe_float(balance_td)

        items = []
        grand_total = abs(amount)

        if region:
            inner_table = region.find("table", class_="jsa_transactions")
            if inner_table:
                for item_row in inner_table.find_all("tr", attrs={"role": "row"}):
                    classes = item_row.get("class") or []
                    if "jsa_table-headers" in classes:
                        continue
                    item_td  = item_row.find("td", attrs={"headers": "item"})
                    qty_td   = item_row.find("td", attrs={"headers": "quantity"})
                    if not item_td or not qty_td:
                        continue
                    name = item_td.get_text(" ", strip=True)
                    if not name:
                        continue
                    try:
                        qty = int(qty_td.get_text(strip=True))
                    except ValueError:
                        continue
                    mod_td      = item_row.find("td", attrs={"headers": "modfier"})
                    unit_td     = item_row.find("td", attrs={"headers": "unit"})
                    promo_td    = item_row.find("td", attrs={"headers": "promos"})
                    subtotal_td = item_row.find("td", attrs={"headers": "subtotal"})
                    items.append({
                        "name":       name,
                        "modifier":   (mod_td.get_text(strip=True) if mod_td else "") or "",
                        "unit_price": _safe_float(unit_td),
                        "qty":        qty,
                        "promo":      (promo_td.get_text(strip=True) if promo_td else "") or "",
                        "subtotal":   _safe_float(subtotal_td),
                    })

                for gt_row in inner_table.find_all("tr", class_="grand-total"):
                    th = gt_row.find("th")
                    if th and "Grand Total" in th.get_text():
                        last_td = gt_row.find_all("td")
                        v = _safe_float(last_td[-1]) if last_td else None
                        if v is not None:
                            grand_total = v
                        break

        if items:
            transactions.append({
                "date":           date_str,
                "location":       location,
                "transaction_id": transaction_id,
                "amount":         amount,
                "balance_after":  balance_after,
                "grand_total":    grand_total,
                "items":          items,
            })

    return transactions
