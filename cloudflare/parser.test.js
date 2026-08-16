// Same traps as tests/test_parser.py, re-verified against the JS port.
// A rewrite is exactly where a fixed bug creeps back in.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseStock } from "./worker.js";

const load = (n) => readFileSync(new URL(`../tests/fixtures/${n}`, import.meta.url), "utf8");

const JANATA = "44333eb5-32ae-4189-85ab-209a8a451249";
const GANDA = "2c249e81-28b0-4a24-b724-24a4b70e8a16";
const PACE_IN = "c6ddbc98-e53e-4abd-ae31-35c6e62781d6";
const PACE_OUT = "34e87cd2-18d8-498e-9699-c158e4461204";

test("out of stock: the two watches being tracked", () => {
  const j = parseStock(load("janata_oos.html"), JANATA);
  assert.equal(j.inStock, false);
  assert.equal(j.name, "HMT Janata Automatic White");
  assert.equal(j.mrp, 14750);
  assert.equal(parseStock(load("gandaberunda_jgsl01_oos.html"), GANDA).inStock, false);
});

// The one test that proves the alarm can ring at all. The Python version of
// this monitor originally required isBuyable, which is false even in stock --
// it would have run for months and never alerted.
test("in-stock page actually reports in stock", () => {
  assert.equal(parseStock(load("pace_ugbkl102_INSTOCK.html"), PACE_IN).inStock, true);
});

// variantsInfo[0] on this page is a DIFFERENT, in-stock variant.
test("does not read variantsInfo[0]", () => {
  assert.equal(parseStock(load("pace_sibling_oos_variant0_instock.html"), PACE_OUT).inStock, false);
});

// This page claims oos:false while being plainly out of stock.
test("ignores the lying oos field", () => {
  assert.equal(parseStock(load("gandaberunda_jgsl01_oos.html"), GANDA).inStock, false);
});

test("recommendation carousels do not leak", () => {
  const html = load("janata_oos.html");
  assert.ok(html.split('"inStock":true').length - 1 >= 3, "fixture precondition");
  assert.equal(parseStock(html, JANATA).inStock, false);
});

test("refuses to guess", () => {
  assert.throws(() => parseStock(load("collection_gandaberunda.html"), GANDA));
  assert.throws(() => parseStock("<html>nothing</html>", JANATA));
  assert.throws(() => parseStock('<script id="__NEXT_DATA__">{not json</script>', JANATA));
  assert.throws(() => parseStock(load("janata_oos.html"), "00000000-0000-0000-0000-000000000000"));
});

test("disagreeing signals raise rather than answer", () => {
  const html = load("pace_ugbkl102_INSTOCK.html").replace(
    'buybuttonswidget-add-to-cart-button">Add to Cart<',
    'buybuttonswidget-add-to-cart-button">Out of Stock<'
  );
  assert.throws(() => parseStock(html, PACE_IN));
});
