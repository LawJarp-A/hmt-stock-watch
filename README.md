# HMT restock monitor

Watches [hmtwatches.store](https://www.hmtwatches.store) and pushes a phone notification
the moment a watch comes back in stock. Runs free on GitHub Actions — no server, and it
works whether your laptop is on, asleep, or in a bag.

Tracking: **HMT Janata Automatic White** (₹14,750) and **HMT Men's Gandaberunda JGSL 01**
(₹2,499), plus every other Janata Automatic / Gandaberunda variant found weekly.

## Usage

```bash
python3 watch.py --dry-run       # print live status, notify nobody
python3 watch.py --test-notify   # prove the phone alert works
python3 watch.py --tier all      # check every discovered variant
python3 watch.py --discover      # re-crawl sitemap for new variants (~5 min)
python3 -m unittest discover -s tests
```

Set `NTFY_TOPIC` in the environment (locally) or as a repo secret (in CI). Without it,
alerts print to stdout instead of being sent.

## Schedule

| Job | When | What |
|---|---|---|
| `watch` hot | every 5 min | the 2 wanted watches |
| `watch` sweep | :07 and :37 | all Janata + Gandaberunda variants |
| `discover` | weekly | re-crawl sitemap for new listings |

Free-tier GitHub cron is best-effort and can drift 5–20 min under load. These watches sit
out of stock for weeks, so that's fine. For sub-minute precision, run `watch.py` on a VPS.

## Three traps this parser exists to avoid

The store's HTML is full of fields that look authoritative and aren't. Each is pinned by a
test in `tests/test_parser.py` — please don't "simplify" these away.

1. **`isBuyable` is `false` even when a watch IS in stock.** Gating the alert on it means
   the alert *never fires*, while the monitor looks perfectly healthy. This is the most
   dangerous bug available here and it is invisible unless you test against a real
   in-stock page.
2. **`variantsInfo[0]` is not the page's product.** Sibling variants share an order, so
   index 0 on an out-of-stock page is often a different, in-stock one → false alarm.
   `primaryProductId` is unrelated to the URL too. Match `sku` against the URL's uuid.
3. **`oos` contradicts reality.** The Gandaberunda page reports `oos:false` while being
   plainly out of stock. Only `inStock` is honest.

The parser also cross-checks the JSON against the rendered buy-button text and raises
`ParseError` if they disagree, so a site redesign produces a loud "monitor is broken"
alert instead of quiet wrong answers.

Collection pages are useless for this — they don't server-render their grid, and both
collection URLs return the same 10 unrelated recommendation items. Product discovery goes
through `sitemap.xml` (270 products; `robots.txt` permits it).

## Failure handling

Silence must mean "out of stock", never "the script died in March":

- 3 consecutive failures on a watch → a **"monitor is broken"** push.
- Once a day at ~09:00 IST → a silent heartbeat confirming it's alive.
- In stock → urgent push, repeated every 30 min until it sells out, so one missed banner
  isn't a missed watch.
