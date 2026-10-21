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
import unicodedata
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGIN_HOST = "https://studentlink.atriumcampus.com"
LOGIN_URL = f"{LOGIN_HOST}/login.php?guest=1&cid=410"
ACCOUNT_HOST = "https://connectobu.atriumcampus.com"
STATEMENT_BASE = f"{ACCOUNT_HOST}/statementdetail.php"
CID = "410"
TIMEOUT = 30  # seconds per request
ACTIVATION_POLLS = 30  # login-check.php attempts, 2 s apart
USER_AGENT = "Mozilla/5.0 (compatible; OBU-Stu-Wrapped/2.0; +https://stu.exposed)"
# Atrium sometimes answers 5xx for a few seconds (the login page 500'd for one student's
# every attempt). Page loads are retried up to 3 times over about 6 s; the login POST is
# not, since repeating it could open a second guest session.
RETRY = Retry(total=3, backoff_factor=1, status_forcelist=(500, 502, 503, 504),
              allowed_methods=frozenset({"GET"}), raise_on_status=False)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=RETRY))
    return session


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


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def obu_email(name: str, student_id: str) -> str:
    """Derive the institutional email: first three last-name letters + Atrium ID."""
    if not re.fullmatch(r"\d{5}", student_id):
        raise ValueError("invalid Atrium student ID")
    parts = name.split()
    while parts and re.sub(r"[^a-z]", "", parts[-1].casefold()) in _NAME_SUFFIXES:
        parts.pop()
    if not parts:
        raise ValueError("Atrium statement has no student name")
    folded = unicodedata.normalize("NFKD", parts[-1]).encode("ascii", "ignore").decode()
    prefix = re.sub(r"[^a-z]", "", folded.casefold())[:3]
    if len(prefix) != 3:
        raise ValueError("student last name has fewer than three letters")
    return f"{prefix}{student_id}@obu.edu"


def student_identity(pages: list[dict], name: str | None) -> dict[str, str]:
    """The canonical ID/email exposed in every Atrium statement page.

    Every page must agree. The masked ``Account Number: XX###`` is deliberately
    ignored because it collides across students.
    """
    ids = set()
    for page in pages:
        soup = BeautifulSoup(page.get("html") or "", "lxml")
        info = soup.find("aside", attrs={"aria-label": "Customer Information"})
        if not info:
            continue
        match = re.search(r"\bAccount\s+(\d{5})\b", info.get_text(" ", strip=True))
        if match:
            ids.add(match.group(1))
    if len(ids) != 1:
        raise ValueError(f"Atrium statements exposed {len(ids)} distinct student IDs")
    student_id = ids.pop()
    return {"student_id": student_id, "email": obu_email(name or "", student_id)}


def fetch_activity(username, password, startdate, enddate, progress=lambda step, msg: None):
    """Log in and return (purchases, swipes, balances, pages) between startdate and enddate
    (ISO dates) across every account the guest can see. Meal-plan accounts ("Resident
    Meals") have no itemized receipts; each -1 row there is a dining-hall swipe. balances
    maps account name to its current balance, e.g. {"Resident Flex": 249.07}; pages is
    each account's raw statement HTML ([{account, acct, html}]) for archiving."""
    purchases, swipes, balances, pages = [], [], {}, []
    with _session() as session:
        progress(2, "Logging in to Flex Bucks…")
        skey = login(session, username, password)
        try:
            accounts = list_accounts(session, skey)
            if not accounts:
                raise NoAccounts("guest login has no viewable accounts")

            progress(3, "Loading your purchase history…")
            for acct, name in accounts:
                resp = session.get(build_statement_url(skey, startdate, enddate, acct), timeout=TIMEOUT)
                resp.raise_for_status()
                if _login_form(resp.text) is not None:
                    raise RuntimeError("Atrium sent the statement request back to the login page")
                pages.append({"account": name, "acct": acct, "html": resp.text.replace(skey, "SKEY")})
                balance = parse_balance(resp.text)
                if balance is not None:
                    balances[name] = balance
                if "meal" in name.lower():
                    swipes += parse_swipes(resp.text)
                else:
                    purchases += parse_transactions(resp.text)
        finally:
            logout(session, skey)

    progress(4, "Reading your receipts…")
    return purchases, swipes, balances, pages


def parse_balance(html) -> float | None:
    """The statement's "Account Summary → Current Balance"."""
    text = BeautifulSoup(html, "lxml").get_text(" ")
    m = re.search(r"Current Balance:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", text)
    return float(m.group(1).replace(",", "")) if m else None


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
            # Meal exchanges at other counters ("Tiger Exp 1") carry an itemized receipt
            # inside <details>; the location is its <summary>, not the whole cell's text.
            label = desc.find("summary") or desc
            swipes.append({"date": date_cell.get_text(" ", strip=True), "location": label.get_text(" ", strip=True)})
    return swipes


def weekly_meal_plans(pages) -> list[str]:
    """Meal-plan accounts that Atrium refills every week (an "Automated Reset" row in the
    statement), e.g. "Resident Meals" or "Voluntary Full Meals". Their balance is this
    week's swipes, not what's left for the semester like a block plan's ("Basic Meals")."""
    weekly = []
    for page in pages:
        if "meal" not in page["account"].lower():
            continue
        rows = BeautifulSoup(page["html"], "lxml").find_all("td", class_="jsa_desc")
        if any(row.get_text(" ", strip=True) == "Automated Reset" for row in rows):
            weekly.append(page["account"])
    return weekly


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
