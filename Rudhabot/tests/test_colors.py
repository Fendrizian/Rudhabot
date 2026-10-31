"""Embed colours come from the house palette in `rudhabot/ui/theme.py`, never from ad-hoc hex literals."""
import pathlib
import re
import unittest

from rudhabot.ui import theme

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "rudhabot"
HEX = re.compile(r"\b0x[0-9A-Fa-f]{6}\b")


class PaletteTest(unittest.TestCase):
    def test_no_colour_literals_outside_the_theme(self):
        offenders = [f"{p.relative_to(PACKAGE)}:{n}: {line.strip()}"
                     for p in PACKAGE.rglob("*.py") if p.name != "theme.py"
                     for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if HEX.search(line)]
        self.assertEqual(offenders, [], "use a Colors.* constant from rudhabot/ui/theme.py")

    def test_palette_is_the_agreed_one(self):
        c = theme.Colors
        self.assertEqual((c.STOCK_MARKET, c.PORTFOLIO, c.BANK, c.DEPOSIT, c.WITHDRAW, c.CLAIRVOYANCE, c.GAMBLING, c.HELP),
                         (0x1ABC9C, 0x11806A, 0x1F8B4C, 0x206694, 0x3498DB, 0x5865F2, 0x99AAB5, 0x607D8B))
        tcg = [c.TCG_DEEP, c.TCG_BLUE, c.TCG_SKY, c.TCG_MIST, c.TCG_CLOUD]
        lum = [0.2126 * (v >> 16) + 0.7152 * (v >> 8 & 255) + 0.0722 * (v & 255) for v in tcg]
        self.assertEqual(lum, sorted(lum), "the TCG palette runs darkest to brightest")


if __name__ == "__main__":
    unittest.main()
