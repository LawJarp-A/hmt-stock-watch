/**
 * HMT restock monitor -- Cloudflare Worker.
 *
 * Two jobs in one script:
 *   scheduled()  every minute: check stock, push to phone on restock
 *   fetch()      serve the status page, rendered live from current state
 *
 * The parser is a direct port of watch.py. The three traps it guards are real
 * and were each observed on the live store -- read the comments in parseStock()
 * before changing anything there.
 */

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const PRODUCT_URL = (id) => `https://www.hmtwatches.store/product/${id}`;
const PRODUCTS_JSON =
  "https://raw.githubusercontent.com/LawJarp-A/hmt-stock-watch/main/products.json";
const RENUDGE = 30 * 60 * 1000; // keep nudging every 30 min while in stock
const SWEEP_EVERY = 30; // minutes between full-variant sweeps
const FAIL_ALERT_AFTER = 3;

class ParseError extends Error {}

/** Returns {name, inStock, mrp} or throws. Never guesses. */
export function parseStock(html, uuid) {
  const m = html.match(
    /<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/
  );
  if (!m) throw new ParseError("no __NEXT_DATA__ block");
  let data;
  try {
    data = JSON.parse(m[1]);
  } catch (e) {
    throw new ParseError(`__NEXT_DATA__ is not valid JSON: ${e.message}`);
  }

  const variants = data?.props?.pageProps?.catalog?.variantsInfo ?? [];
  // TRAP 1: variantsInfo[0] is NOT this page's product. Sibling variants share
  // an order, so index 0 on an out-of-stock page is often a DIFFERENT, in-stock
  // variant -> false alarm. primaryProductId is unrelated to the URL too.
  // Matching sku against the URL uuid is the only correct anchor.
  const v = variants.find((x) => x.sku === uuid);
  if (!v)
    throw new ParseError(`no variant matching ${uuid} (${variants.length} present)`);

  const attrs = v.attributes ?? {};
  const avail = attrs.buyingOptions?.singlePurchase?.availability ?? {};
  // TRAP 2: isBuyable is false EVEN WHEN IN STOCK -- gating on it means the
  // alert never fires, ever. TRAP 3: `oos` contradicts reality (Gandaberunda
  // reports oos:false while out of stock). inStock is the only honest field.
  const jsonSays = avail.inStock === true;

  // Second, independent signal: the rendered button. If a redesign changes the
  // JSON shape or the copy, the two disagree and we refuse to answer rather
  // than quietly reporting a wrong state.
  const buttons = [
    ...html.matchAll(/buybuttonswidget-add-to-cart-button"[^>]*>([^<]+)</g),
  ].map((b) => b[1].trim());
  if (buttons.length !== 1)
    throw new ParseError(`expected 1 buy button, found ${buttons.length}`);
  const htmlSays = buttons[0] === "Add to Cart";

  if (jsonSays !== htmlSays)
    throw new ParseError(`signals disagree: json=${jsonSays} button=${buttons[0]}`);

  return { name: attrs.name, inStock: jsonSays, mrp: attrs.price?.mrp ?? null };
}

async function fetchPage(url, tries = 3) {
  for (let n = 0; n < tries; n++) {
    try {
      const r = await fetch(url, {
        headers: { "User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9" },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.text();
    } catch (e) {
      if (n === tries - 1) throw e;
      await new Promise((r) => setTimeout(r, 500 * 2 ** n));
    }
  }
}

async function notify(env, { title, body, url, priority = "default", tags = "watch" }) {
  if (!env.NTFY_TOPIC) return;
  const headers = { Title: title, Priority: priority, Tags: tags };
  if (url) {
    headers.Click = url;
    headers.Actions = `view, Buy now, ${url}`;
  }
  await fetch(`https://ntfy.sh/${env.NTFY_TOPIC}`, { method: "POST", body, headers });
}

async function products(sweep) {
  const fallback = [
    { id: "44333eb5-32ae-4189-85ab-209a8a451249", name: "HMT Janata Automatic White" },
    { id: "2c249e81-28b0-4a24-b724-24a4b70e8a16", name: "HMT Men's Gandaberunda JGSL 01" },
  ];
  try {
    const cfg = await (await fetch(PRODUCTS_JSON, { cf: { cacheTtl: 3600 } })).json();
    const hot = cfg.hot?.length ? cfg.hot : fallback;
    if (!sweep) return hot;
    // Sweep the discovered variants too, de-duplicated against the hot list.
    const seen = new Set(hot.map((p) => p.id));
    return hot.concat((cfg.all ?? []).filter((p) => !seen.has(p.id)));
  } catch {
    return fallback;
  }
}

export async function check(env, sweep) {
  const state = JSON.parse((await env.STATE.get("state")) || "{}");
  const before = JSON.stringify(state);
  const items = await products(sweep);

  for (const item of items) {
    const url = PRODUCT_URL(item.id);
    const st = (state[item.id] ??= { status: "unknown", fails: 0, lastNotified: 0 });
    let s;
    try {
      s = parseStock(await fetchPage(url), item.id);
    } catch (e) {
      st.fails = (st.fails || 0) + 1;
      // A dead monitor you believe is alive is the real failure mode.
      if (st.fails === FAIL_ALERT_AFTER)
        await notify(env, {
          title: "HMT monitor is broken",
          body: `${item.name}: ${e.message}`,
          url,
          priority: "high",
          tags: "warning",
        });
      continue;
    }

    st.fails = 0;
    st.name = s.name;
    st.mrp = s.mrp;
    const was = st.status;
    const due = Date.now() - (st.lastNotified || 0) > RENUDGE;

    if (s.inStock && (was !== "in_stock" || due)) {
      await notify(env, {
        title: `IN STOCK: ${s.name}`,
        body: `Rs.${s.mrp} -- tap to buy before it's gone.`,
        url,
        priority: "urgent",
        tags: "rotating_light",
      });
      st.lastNotified = Date.now();
    } else if (was === "in_stock" && !s.inStock) {
      await notify(env, { title: "Gone again", body: `${s.name} is out of stock.`, url, priority: "low", tags: "disappointed" });
    }
    st.status = s.inStock ? "in_stock" : "out_of_stock";
    st.checked = Date.now();
  }

  // KV free tier allows 1000 writes/day; a write every minute would exceed it.
  // Status rarely changes, but `checked` does -- so write at most every 5 min
  // unless something meaningful changed.
  const meta = (state._meta ??= {});
  const changed = JSON.stringify({ ...state, _meta: null }) !== JSON.stringify({ ...JSON.parse(before || "{}"), _meta: null });
  if (changed || Date.now() - (meta.written || 0) > 5 * 60 * 1000) {
    meta.written = Date.now();
    await env.STATE.put("state", JSON.stringify(state));
  }
  return state;
}

export default {
  async scheduled(event, env, ctx) {
    const sweep = new Date(event.scheduledTime).getUTCMinutes() % SWEEP_EVERY === 7;
    ctx.waitUntil(check(env, sweep));
  },

  async fetch(request, env) {
    // /test-alert?key=<ntfy topic> proves the Worker's own alert path works.
    // Worth having permanently: this monitor's job is to break silence, so the
    // ability to confirm it can still shout is not a debug convenience.
    // Guarded by the topic itself, which is already the shared secret.
    const url = new URL(request.url);
    if (url.pathname === "/test-alert") {
      if (url.searchParams.get("key") !== env.NTFY_TOPIC)
        return new Response("nope", { status: 403 });
      await notify(env, {
        title: "Worker alert test",
        body: "The Cloudflare monitor can reach your phone.",
        url: "https://hmt-watch.prajwalanagani.workers.dev",
        priority: "high",
        tags: "white_check_mark",
      });
      return new Response("sent\n");
    }

    const state = JSON.parse((await env.STATE.get("state")) || "{}");
    const { render } = await import("./page.js");
    return new Response(render(state), {
      headers: {
        "content-type": "text/html;charset=utf-8",
        "cache-control": "no-store",
      },
    });
  },
};
