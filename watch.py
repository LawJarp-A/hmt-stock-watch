#!/usr/bin/env python3
"""HMT restock monitor -- polls hmtwatches.store, pushes to phone via ntfy.

Standard library only, on purpose: no pip install in CI, nothing to break.
Read the trap comments in parse_stock() before changing it.
"""
import argparse, gzip, json, os, pathlib, random, re, sys, time, urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
ROOT = pathlib.Path(__file__).parent
PRODUCTS, STATE = ROOT / "products.json", ROOT / "state.json"
TOPIC = os.environ.get("NTFY_TOPIC", "")
WANTED = re.compile(r"janata|gandaberunda", re.I)
PRODUCT_URL = "https://www.hmtwatches.store/product/{}"
IST = timezone(timedelta(hours=5, minutes=30))
RENUDGE = 30 * 60          # keep nudging every 30 min while in stock
FAIL_ALERT_AFTER = 3       # consecutive failures before crying for help


class ParseError(Exception):
    """Raised whenever the answer is not certain. Never guess in-stock."""


@dataclass
class Stock:
    name: str
    in_stock: bool
    mrp: int | None
    raw_oos: bool | None
    raw_is_buyable: bool | None


def parse_stock(html: str, uuid: str) -> Stock:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ParseError("no __NEXT_DATA__ block")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise ParseError(f"__NEXT_DATA__ is not valid JSON: {e}") from e

    variants = data.get("props", {}).get("pageProps", {}).get("catalog", {}).get("variantsInfo") or []
    # TRAP 1: variantsInfo[0] is NOT this page's product. Sibling variants are
    # listed in a shared order, so index 0 on an out-of-stock page is often a
    # DIFFERENT, in-stock variant -> false alarm. primaryProductId is unrelated
    # to the URL too. Matching sku against the URL uuid is the only correct anchor.
    v = next((x for x in variants if x.get("sku") == uuid), None)
    if v is None:
        raise ParseError(f"no variant matching {uuid} ({len(variants)} present)")

    attrs = v.get("attributes") or {}
    avail = (attrs.get("buyingOptions") or {}).get("singlePurchase", {}).get("availability") or {}
    # TRAP 2: isBuyable is false EVEN WHEN IN STOCK -- gating on it means the
    # alert never fires, ever. TRAP 3: `oos` contradicts reality (Gandaberunda
    # says oos:false while out of stock). inStock is the only honest field.
    json_says = avail.get("inStock") is True

    # Second, independent signal: the rendered button. If a redesign changes the
    # JSON shape or the copy, the two disagree and we refuse to answer rather
    # than quietly reporting a wrong state.
    buttons = re.findall(r'buybuttonswidget-add-to-cart-button"[^>]*>([^<]+)<', html)
    if len(buttons) != 1:
        raise ParseError(f"expected 1 buy button, found {len(buttons)}")
    html_says = buttons[0].strip() == "Add to Cart"

    if json_says != html_says:
        raise ParseError(f"signals disagree: json={json_says} button={buttons[0].strip()!r}")

    return Stock(attrs.get("name"), json_says, (attrs.get("price") or {}).get("mrp"),
                 attrs.get("oos"), avail.get("isBuyable"))


def fetch(url: str, tries: int = 3) -> str:
    """The store 403s without a browser User-Agent.

    We ask for gzip: pages are ~500KB raw but ~70KB compressed. Same data, a
    fraction of the bandwidth off someone else's servers.
    """
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "ignore")
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(2 ** n + random.random())


def notify(title: str, body: str, url: str = "", priority: str = "default", tags: str = "watch") -> None:
    if not TOPIC:
        print(f"[no NTFY_TOPIC] {title}: {body}")
        return
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Buy now, {url}"
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=body.encode(), headers=headers), timeout=15)
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)


def check(items: list, state: dict, dry: bool) -> None:
    for i, item in enumerate(items):
        if i:
            time.sleep(random.uniform(1, 3))
        uuid, url = item["id"], PRODUCT_URL.format(item["id"])
        st = state.setdefault(uuid, {"status": "unknown", "fails": 0, "last_notified": 0})
        try:
            s = parse_stock(fetch(url), uuid)
        except Exception as e:
            st["fails"] += 1
            print(f"FAIL {item.get('name', uuid)}: {e}", file=sys.stderr)
            # A dead monitor you believe is alive is the real failure mode.
            if st["fails"] == FAIL_ALERT_AFTER and not dry:
                notify("HMT monitor is broken", f"{item.get('name', uuid)}: {e}", url, "high", "warning")
            continue

        st["fails"] = 0
        st["name"], st["mrp"] = s.name, s.mrp
        was, now = st["status"], "in_stock" if s.in_stock else "out_of_stock"
        print(f"{'IN STOCK' if s.in_stock else 'out     '}  {s.name}  Rs.{s.mrp}")
        if dry:
            continue

        due = time.time() - st["last_notified"] > RENUDGE
        if s.in_stock and (was != "in_stock" or due):
            notify(f"IN STOCK: {s.name}", f"Rs.{s.mrp} -- tap to buy before it's gone.", url, "urgent", "rotating_light")
            st["last_notified"] = time.time()
        elif was == "in_stock" and not s.in_stock:
            notify("Gone again", f"{s.name} is out of stock.", url, "low", "disappointed")
        st["status"], st["checked"] = now, time.time()


def heartbeat(items: list, state: dict) -> None:
    """One quiet ping a day so silence provably means 'out of stock', not 'script died'."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    meta = state.setdefault("_meta", {})
    if meta.get("heartbeat") == today or datetime.now(IST).hour < 9:
        return
    meta["heartbeat"] = today
    live = [s["name"] for k, s in state.items() if k != "_meta" and s.get("status") == "in_stock"]
    notify("HMT monitor alive", f"{len(items)} watches tracked. " + (f"In stock: {', '.join(live)}" if live else "All out of stock."), priority="min", tags="heartbeat")


def discover() -> list:
    """Crawl sitemap.xml (270 products; robots.txt allows it) and keep the ones we want.

    Collection pages can't be used -- they don't server-render their grid, and
    both return the same 10 unrelated recommendation items.
    """
    uuids = sorted(set(re.findall(r"/product/([0-9a-f-]{36})", fetch("https://www.hmtwatches.store/sitemap.xml"))))
    print(f"sitemap: {len(uuids)} products")
    found, errors = [], []
    for n, u in enumerate(uuids, 1):
        try:
            s = parse_stock(fetch(PRODUCT_URL.format(u)), u)
        except Exception as e:
            errors.append(f"{u}: {e}")
            continue
        if s.name and WANTED.search(s.name):
            found.append({"id": u, "name": s.name})
            print(f"  [{n}/{len(uuids)}] {s.name}")
        time.sleep(random.uniform(0.3, 0.8))
    # Unreadable pages are silently skipped -- if a Janata variant is among them
    # it never gets watched. Say so loudly rather than reporting a clean run.
    if errors:
        print(f"\n{len(errors)} of {len(uuids)} pages unreadable (a wanted watch could be hiding here):", file=sys.stderr)
        for e in errors[:10]:
            print(f"  {e}", file=sys.stderr)
    return found


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", choices=["hot", "all"], default="hot")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--discover", action="store_true")
    p.add_argument("--test-notify", action="store_true")
    a = p.parse_args()

    if a.test_notify:
        notify("HMT monitor test", "If you can read this on your phone, alerts work.", PRODUCT_URL.format("44333eb5-32ae-4189-85ab-209a8a451249"), "high", "white_check_mark")
        return print("sent")

    if a.discover:
        found = discover()
        cfg = json.loads(PRODUCTS.read_text()) if PRODUCTS.exists() else {"hot": [], "all": []}
        cfg["all"] = found
        PRODUCTS.write_text(json.dumps(cfg, indent=2) + "\n")
        return print(f"wrote {len(found)} watches to products.json")

    cfg = json.loads(PRODUCTS.read_text())
    items = cfg["hot"] if a.tier == "hot" else (cfg.get("all") or cfg["hot"])
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    check(items, state, a.dry_run)
    if not a.dry_run:
        heartbeat(items, state)
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
