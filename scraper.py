import sys
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


def _log(*args):
    print(*args, flush=True, file=sys.stdout)

LOGIN_URL = "https://studentlink.atriumcampus.com/login.php?guest=1&cid=410"
STATEMENT_BASE = "https://connectobu.atriumcampus.com/statementdetail.php"
CID = "410"
CREDENTIAL_TIMEOUT = 300  # 5 minutes


def wait_for_credentials(job_id, progress_callback):
    """
    Wait for credentials to be POSTed from Cloudflare Email Worker.
    Polls every 2 seconds for up to CREDENTIAL_TIMEOUT seconds.
    Returns (username, password) on success, raises TimeoutError otherwise.
    """
    from jobs import get_credentials

    start_time = time.time()
    poll_count = 0

    while True:
        elapsed = int(time.time() - start_time)
        if elapsed >= CREDENTIAL_TIMEOUT:
            raise TimeoutError(
                f"Didn't receive Flex Bucks access email after {CREDENTIAL_TIMEOUT // 60} minutes. "
                "Make sure you added the correct email in step 2 and try again."
            )

        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
        step = 2 if poll_count < 5 else 3
        progress_callback(step, f"Waiting for Flex Bucks access... ({time_str} elapsed)")

        creds = get_credentials(job_id)
        if creds:
            return creds["username"], creds["password"]

        poll_count += 1
        time.sleep(2)


def extract_skey(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    skey_list = params.get("skey", [])
    if not skey_list or not skey_list[0]:
        raise RuntimeError(f"skey not found in URL: {url}")
    return skey_list[0]


def build_statement_url(skey, startdate, enddate, acct="1"):
    params = {"cid": CID, "skey": skey, "startdate": startdate, "enddate": enddate, "acct": acct}
    return f"{STATEMENT_BASE}?{urlencode(params)}"


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


def run_scrape_job_with_skey(job_id, startdate, enddate, skey, acct="1", login_url=None):
    from jobs import update_job_progress, complete_job, fail_job
    browser = None
    try:
        _log(f"[scraper] job={job_id} start={startdate} end={enddate}")
        with sync_playwright() as p:
            update_job_progress(job_id, 1, "Launching browser...")
            _log(f"[scraper] launching chromium")
            browser = p.chromium.launch(headless=True, slow_mo=0)
            page = browser.new_context().new_page()

            # Visit the user's original URL first so Atrium can set session cookies
            if login_url:
                _log(f"[scraper] establishing session via login_url")
                page.goto(login_url, wait_until="networkidle")
                _log(f"[scraper] session page loaded, url={page.url}")

            update_job_progress(job_id, 2, "Loading your transaction history...")
            statement_url = build_statement_url(skey, startdate, enddate, acct)
            _log(f"[scraper] navigating to statement URL")
            page.goto(statement_url, wait_until="networkidle")
            _log(f"[scraper] page loaded, final url: {page.url}")
            update_job_progress(job_id, 3, "Expanding transaction details...")
            try:
                page.eval_on_selector_all("details", "els => els.forEach(e => e.open = true)")
                page.wait_for_timeout(500)
            except Exception:
                pass
            update_job_progress(job_id, 4, "Parsing your food data...")
            html = page.content()
            _log(f"[scraper] page html length: {len(html)}")
            if len(html) < 500:
                _log(f"[scraper] short response body: {html!r}")
            transactions = parse_transactions(html)
            _log(f"[scraper] parsed {len(transactions)} transactions")
            browser.close()
            browser = None
        update_job_progress(job_id, 5, "Finishing up...")
        metadata = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "startdate": startdate, "enddate": enddate, "cid": CID, "acct": acct,
        }
        complete_job(job_id, transactions, metadata)
        _log(f"[scraper] job={job_id} complete, {len(transactions)} transactions")
    except Exception as exc:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        _log(f"[scraper] job={job_id} FAILED: {exc}")
        _log(traceback.format_exc())
        fail_job(job_id, str(exc))


def run_scrape_job(job_id, startdate, enddate, plus_address, acct="1"):
    from jobs import update_job_progress, complete_job, fail_job
    browser = None
    try:
        update_job_progress(job_id, 1, "Connecting to your Flex Bucks account...")

        def _progress(step, msg):
            update_job_progress(job_id, step, msg)

        username, password = wait_for_credentials(job_id, _progress)
        update_job_progress(job_id, 5, "Launching browser...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, slow_mo=0)
            page = browser.new_context().new_page()
            update_job_progress(job_id, 6, "Logging in to Atrium...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.fill("#loginphrase", username)
            page.fill("input[type='password']", password)
            page.click("input[type='submit'].jsa_submit-form")
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeoutError:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            redirected_url = page.url
            if "skey=" not in redirected_url:
                raise RuntimeError("Login failed — check the credentials in the email.")
            skey = extract_skey(redirected_url)
            update_job_progress(job_id, 8, "Loading your transaction history...")
            page.goto(build_statement_url(skey, startdate, enddate, acct), wait_until="networkidle")
            update_job_progress(job_id, 9, "Expanding transaction details...")
            try:
                page.eval_on_selector_all("details", "els => els.forEach(e => e.open = true)")
                page.wait_for_timeout(500)
            except Exception:
                pass
            update_job_progress(job_id, 10, "Parsing your food data...")
            html = page.content()
            transactions = parse_transactions(html)
            browser.close()
            browser = None
        metadata = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "startdate": startdate, "enddate": enddate, "cid": CID, "acct": acct,
        }
        complete_job(job_id, transactions, metadata)
    except Exception as exc:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        fail_job(job_id, str(exc))
