"""Render a traceable, synthetic USD account discrepancy exhibit from published evidence.

The source is the published bridge rows, not hand-entered figures or fabricated
enterprise account values. This graphic is a generated exhibit, not a UI capture.
"""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/evidence/modeling-cases.json"
OUTPUT = ROOT / "docs/images/account-discrepancy-bridge.jpg"


def main():
    evidence = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = evidence["bounded_discrepancy_examples"]
    expected = {"Enterprise": 8, "Mid-market": 7, "SMB": 5}
    actual = Counter(x["segment"] for x in rows)
    if actual != expected:
        raise AssertionError(f"Invalid segment mix for account exhibit: {actual}")
    selected = []
    for segment in ("Enterprise", "Mid-market", "SMB"):
        chosen = [x for x in rows if x["segment"] == segment][:4]
        selected.extend(chosen)

    W, H = 1580, 870
    image = Image.new("RGB", (W, H), "#0b101b")
    d = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def font(size, bold=False):
        p = font_path.replace(".ttf", "-Bold.ttf") if bold else font_path
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            return ImageFont.load_default()

    title, sub, header, body, foot = font(32, True), font(19), font(16, True), font(16), font(17)
    d.text((38, 26), "Why the numbers differ: account-level evidence", font=title, fill="#f4f8ff")
    d.text(
        (40, 78),
        "12 of 20 high-variance synthetic accounts, across three segments | USD | Q1 2026",
        font=sub,
        fill="#aebed9",
    )
    columns = [
        (40, "ACCOUNT"),
        (280, "SEGMENT"),
        (490, "BOOKINGS"),
        (680, "NET INVOICES"),
        (890, "CASH"),
        (1060, "BOOK - BILL"),
        (1290, "BILL - CASH"),
    ]
    d.rectangle((32, 130, 1548, 177), fill="#1d2b42")
    for x, lbl in columns:
        d.text((x, 144), lbl, font=header, fill="#dbe8fb")

    def usd(v):
        number = Decimal(str(v))
        return ("-$" if number < 0 else "$") + f"{abs(number):,.0f}"

    for i, row in enumerate(selected):
        y = 185 + i * 47
        d.rectangle((32, y, 1548, y + 43), fill="#142036" if i % 2 == 0 else "#101a2b")
        cells = [
            row["account_name"].replace("Synthetic Account ", "Account "),
            row["segment"],
            usd(row["bookings"]),
            usd(row["invoiced"]),
            usd(row["cash"]),
            usd(row["bookings_less_invoices"]),
            usd(row["invoices_less_cash"]),
        ]
        for (x, _), val in zip(columns, cells):
            d.text((x, y + 11), val, font=body, fill="#eef4ff")

    d.text(
        (40, 780),
        "Rows: highest material gap per segment. Full 60,000-account bridge remains in the local warehouse.",
        font=foot,
        fill="#cad9ed",
    )
    d.text(
        (40, 815),
        "Period flow differences are not accounts-receivable balances. Reproduced from recorded model outputs.",
        font=foot,
        fill="#9eafc9",
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, "JPEG", quality=93, optimize=True)
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "selected": len(selected),
                "segments": dict(actual),
                "source": str(SOURCE.relative_to(ROOT)),
            }
        )
    )


if __name__ == "__main__":
    main()
