"""Parser tests.

Every case here pins a bug that was actually observed on the live store during
investigation -- not a hypothetical one. The comments name the trap so a future
reader knows why the test exists and does not "simplify" it away.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from watch import ParseError, parse_stock  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

JANATA = "44333eb5-32ae-4189-85ab-209a8a451249"
GANDABERUNDA_JGSL01 = "2c249e81-28b0-4a24-b724-24a4b70e8a16"
PACE_INSTOCK = "c6ddbc98-e53e-4abd-ae31-35c6e62781d6"
PACE_SIBLING_OOS = "34e87cd2-18d8-498e-9699-c158e4461204"


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8", errors="ignore")


class TestOutOfStock(unittest.TestCase):
    """Baseline: the two watches Prajwal wants, both out of stock when captured."""

    def test_janata_automatic_is_out_of_stock(self):
        r = parse_stock(load("janata_oos.html"), JANATA)
        self.assertFalse(r.in_stock)
        self.assertEqual(r.name, "HMT Janata Automatic White")
        self.assertEqual(r.mrp, 14750)

    def test_gandaberunda_jgsl01_is_out_of_stock(self):
        r = parse_stock(load("gandaberunda_jgsl01_oos.html"), GANDABERUNDA_JGSL01)
        self.assertFalse(r.in_stock)
        self.assertEqual(r.name, "HMT Men's Gandaberunda JGSL 01")


class TestInStockActuallyFires(unittest.TestCase):
    """The single most important test in this suite.

    The first draft of this monitor required `isBuyable` to be true. On the live
    store `isBuyable` is false EVEN WHEN THE WATCH IS IN STOCK, so that monitor
    would have run every 5 minutes for months and never once alerted -- looking
    perfectly healthy the whole time.

    This test is the only thing proving the alarm can ring at all. If it ever
    starts failing, the monitor is worthless regardless of what else passes.
    """

    def test_in_stock_page_reports_in_stock(self):
        r = parse_stock(load("pace_ugbkl102_INSTOCK.html"), PACE_INSTOCK)
        self.assertTrue(r.in_stock, "in-stock page must report in stock -- the alert path is dead otherwise")

    def test_is_buyable_is_false_even_in_stock(self):
        """Documents the trap directly, so nobody 'fixes' the parser by re-adding isBuyable."""
        r = parse_stock(load("pace_ugbkl102_INSTOCK.html"), PACE_INSTOCK)
        self.assertTrue(r.in_stock)
        self.assertFalse(r.raw_is_buyable, "isBuyable is false in stock; it must never gate the alert")


class TestVariantAnchoring(unittest.TestCase):
    """variantsInfo[0] is NOT this page's product.

    Both Pace pages carry the same 5 sibling variants in the same order. Reading
    index 0 makes the OUT-OF-STOCK page report IN STOCK -- a 3am false alarm.
    Only `sku == <uuid from the URL>` picks the right variant.
    """

    def test_out_of_stock_page_whose_variant0_is_in_stock(self):
        html = load("pace_sibling_oos_variant0_instock.html")
        r = parse_stock(html, PACE_SIBLING_OOS)
        self.assertFalse(r.in_stock, "must read the URL's variant, not variantsInfo[0]")

    def test_uuid_selects_the_variant(self):
        """Proves the uuid does the selecting, not position.

        The two Pace fixtures contain the SAME five variants in the same order.
        The only thing distinguishing them is which sku the URL names -- and the
        parser reports opposite answers for them, so it cannot be reading index 0.
        """
        oos = parse_stock(load("pace_sibling_oos_variant0_instock.html"), PACE_SIBLING_OOS)
        ins = parse_stock(load("pace_ugbkl102_INSTOCK.html"), PACE_INSTOCK)
        self.assertFalse(oos.in_stock)
        self.assertTrue(ins.in_stock)

    def test_asking_a_page_about_a_foreign_variant_refuses(self):
        """Cross-checking is page-level, so we cannot answer for a sibling variant.

        Refusing is correct: the rendered button describes the page's own product,
        so any answer about a different sku would be unverified. Better a loud
        ParseError than a confident wrong one.
        """
        with self.assertRaises(ParseError):
            parse_stock(load("pace_sibling_oos_variant0_instock.html"), PACE_INSTOCK)

    def test_uuid_not_present_raises(self):
        with self.assertRaises(ParseError):
            parse_stock(load("janata_oos.html"), "00000000-0000-0000-0000-000000000000")


class TestKnownLiars(unittest.TestCase):
    """Fields that look authoritative and are not."""

    def test_oos_field_is_ignored(self):
        """Gandaberunda JGSL 01 reports oos:false while being plainly Out of Stock."""
        r = parse_stock(load("gandaberunda_jgsl01_oos.html"), GANDABERUNDA_JGSL01)
        self.assertFalse(r.raw_oos, "fixture precondition: this page really does claim oos:false")
        self.assertFalse(r.in_stock, "...and the parser must still say out of stock")

    def test_recommendation_carousel_nodes_do_not_leak(self):
        """The Janata page has 21 availability nodes, 3 of them inStock:true --
        all from recommendation widgets. A loose regex fires a false alarm here."""
        html = load("janata_oos.html")
        self.assertGreaterEqual(html.count('"inStock":true'), 3, "fixture precondition")
        self.assertFalse(parse_stock(html, JANATA).in_stock)


class TestRefusesToGuess(unittest.TestCase):
    """Silence must mean 'out of stock', never 'the parser broke and shrugged'."""

    def test_collection_page_is_not_a_product(self):
        with self.assertRaises(ParseError):
            parse_stock(load("collection_gandaberunda.html"), GANDABERUNDA_JGSL01)

    def test_missing_next_data_raises(self):
        with self.assertRaises(ParseError):
            parse_stock("<html><body>nothing here</body></html>", JANATA)

    def test_malformed_json_raises(self):
        bad = '<script id="__NEXT_DATA__" type="application/json">{not json</script>'
        with self.assertRaises(ParseError):
            parse_stock(bad, JANATA)

    def test_signals_disagreeing_raises(self):
        """JSON says in stock, button says Out of Stock -> refuse to answer.

        Built by taking the real in-stock page and flipping only the button text,
        which is what a site redesign or A/B test would look like.
        """
        html = load("pace_ugbkl102_INSTOCK.html")
        self.assertIn('buybuttonswidget-add-to-cart-button">Add to Cart<', html)
        tampered = html.replace(
            'buybuttonswidget-add-to-cart-button">Add to Cart<',
            'buybuttonswidget-add-to-cart-button">Out of Stock<',
        )
        with self.assertRaises(ParseError):
            parse_stock(tampered, PACE_INSTOCK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
