"""Flex Bucks (Atrium) transaction scraper — plain HTTP, no browser.

Atrium's guest login is an ordinary form post: GET the login page to get a
session key (skey, also the jsa_session cookie) and POST the guest
email/password with it. Success is a JS redirect to a "Please wait" page that
activates the session; its script polls login-check.php until the skey is
usable. Statement pages then take the skey as a query parameter. Session keys
are bound to the client IP (see ben.page/wrapped), so every request runs here.
Guests can only hold a few sessions at once ("Login sessions exceeded
allowance"), so each run logs out when it is done.
"""
import re
import time
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

LOGIN_HOST = "https://studentlink.atriumcampus.com"
LOGIN_URL = f"{LOGIN_HOST}/login.php?guest=1&cid=410"
ACCOUNT_HOST = "https://connectobu.atriumcampus.com"
STATEMENT_BASE = f"{ACCOUNT_HOST}/statementdetail.php"
CID = "410"
TIMEOUT = 30  # seconds per request
ACTIVATION_POLLS = 30  # login-check.php attempts, 2 s apart
USER_AGENT = "Mozilla/5.0 (compatible; OBU-Stu-Wrapped/2.0; +https://wrapped.drew.place)"


class LoginFailed(Exception):
    """Atrium rejected the guest email/password."""


class LoginLimited(Exception):
    """Atrium refused the login for now ("Login sessions exceeded allowance")."""


class NoAccounts(Exception):
    """The guest login works but Atrium shows it no accounts to read."""


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
    # Atrium answers some refusals with an error page that has no login form.
    error = re.search(r"Please fix the following errors:\s*(.+?)\s*(?:Footer Menu|$)",
                      BeautifulSoup(resp.text, "lxml").get_text(" "), re.S)
    if error:
        message = re.sub(r"\s+", " ", error.group(1)).strip()
        if "exceeded allowance" in message.lower():
            raise LoginLimited(message)
        raise LoginFailed(message)

    # Success is a JS redirect to a "Please wait while we access your account"
    # page; visiting it starts activation, and the page's script polls
    # login-check.php until it reports 1 (ready) or -1 (refused).
    skey = fields.get("skey")
    hop = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", resp.text)
    if not skey or not hop:
        raise RuntimeError("Atrium login page changed: no session key or redirect after login")
    session.get(urljoin(resp.url, hop.group(1)), timeout=TIMEOUT).raise_for_status()
    _wait_for_activation(session, skey)
    return skey


def _wait_for_activation(session: requests.Session, skey: str) -> None:
    for _ in range(ACTIVATION_POLLS):
        resp = session.get(f"{LOGIN_HOST}/login-check.php", params={"cid": CID, "skey": skey}, timeout=TIMEOUT)
        resp.raise_for_status()
        status = re.search(r"<message>\s*(-?\d+)\s*</message>", resp.text)
        if not status or status.group(1) == "-1":
            raise LoginFailed("Atrium refused to activate the guest session")
        if status.group(1) == "1":
            return
        time.sleep(2)
    raise RuntimeError("Atrium never finished activating the guest session")


def logout(session: requests.Session, skey: str) -> None:
    """End the Atrium session; guests have a cap on concurrent sessions."""
    try:
        session.get(f"{ACCOUNT_HOST}/logout.php", params={"skey": skey, "cid": CID}, timeout=TIMEOUT)
    except requests.RequestException:
        pass


def build_statement_url(skey, startdate, enddate, acct):
    params = {"cid": CID, "skey": skey, "startdate": startdate, "enddate": enddate, "acct": acct}
    return f"{STATEMENT_BASE}?{urlencode(params)}"


def list_accounts(session: requests.Session, skey: str) -> list[tuple[str, str]]:
    """(id, name) of each account the guest may view, from the statement page's account picker,
    e.g. ("1", "Resident Flex"), ("2", "Resident Meals")."""
    resp = session.get(f"{ACCOUNT_HOST}/statementnew.php", params={"skey": skey, "cid": CID}, timeout=TIMEOUT)
    resp.raise_for_status()
    picker = BeautifulSoup(resp.text, "lxml").find("select", attrs={"name": "acctto"})
    if not picker:
        return []
    return [(o["value"], o.get_text(strip=True)) for o in picker.find_all("option") if o.get("value")]


def fetch_activity(username, password, startdate, enddate, progress=lambda step, msg: None):
    """Log in and return (purchases, swipes) between startdate and enddate (ISO dates)
    across every account the guest can see. Meal-plan accounts ("Resident Meals")
    have no itemized receipts; each -1 row there is a dining-hall swipe."""
    purchases, swipes = [], []
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        progress(2, "Logging in to Flex Bucks...")
        skey = login(session, username, password)
        try:
            accounts = list_accounts(session, skey)
            if not accounts:
                raise NoAccounts("guest login has no viewable accounts")

            progress(3, "Loading your transaction history...")
            for acct, name in accounts:
                resp = session.get(build_statement_url(skey, startdate, enddate, acct), timeout=TIMEOUT)
                resp.raise_for_status()
                if _login_form(resp.text) is not None:
                    raise RuntimeError("Atrium sent the statement request back to the login page")
                if "meal" in name.lower():
                    swipes += parse_swipes(resp.text)
                else:
                    purchases += parse_transactions(resp.text)
        finally:
            logout(session, skey)

    progress(4, "Parsing your food data...")
    return purchases, swipes


def parse_swipes(html):
    """Meal swipes from a meal-plan statement: [{date, location}] for each -1 row
    (weekly "Automated Reset" and deposits are positive and skipped)."""
    swipes = []
    for date_cell in BeautifulSoup(html, "lxml").find_all("th", class_="jsa_month"):
        row = date_cell.parent
        desc = row.find("td", class_="jsa_desc")
        amount = row.find("td", class_="jsa_amount")
        if not desc or not amount:
            continue
        try:
            value = float(amount.get_text(" ", strip=True).split()[0])
        except (ValueError, IndexError):
            continue
        if value < 0:
            swipes.append({"date": date_cell.get_text(" ", strip=True), "location": desc.get_text(" ", strip=True)})
    return swipes


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
