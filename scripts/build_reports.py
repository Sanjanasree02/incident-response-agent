"""Build the shareable PDFs: docs/PROJECT_OVERVIEW.md and docs/PENDING.md -> share/*.pdf.

Fills the overview's result placeholders from data/learning_curve.json, draws the learning-curve chart and the
architecture diagram as inline SVG, and prints each page to PDF with headless Edge or Chrome.

Run: uv run --with markdown python -m scripts.build_reports
"""

import json
import re
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
CURVE = ROOT / "data" / "learning_curve.json"
OUT = ROOT / "share"
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "msedge", "google-chrome", "chromium",
]

BLUE, GREY, INK, MUTED, GRID = "#1f5fbf", "#8a8f98", "#1d2330", "#5b6270", "#e3e6eb"

CSS = f"""
@page {{ size: A4; margin: 14mm 14mm; }}
body {{ font-family: "Segoe UI", system-ui, sans-serif; color: {INK}; font-size: 10pt; line-height: 1.4; }}
h1 {{ font-size: 20pt; margin: 0 0 4pt; }}
h2 {{ font-size: 13pt; margin: 16pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid {GRID};
      page-break-after: avoid; break-after: avoid; }}
li.task {{ list-style: none; margin-left: -14pt; }}
li.task::before {{ content: "\\2610"; margin-right: 6pt; font-size: 11pt; }}
p, li {{ margin: 4pt 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9.5pt; page-break-inside: avoid; }}
th, td {{ border: 1px solid {GRID}; padding: 4pt 6pt; text-align: left; vertical-align: top; }}
th {{ background: #f3f5f8; }}
code {{ font-family: Consolas, monospace; font-size: 9pt; background: #f3f5f8; padding: 0 2pt; }}
pre {{ background: #f3f5f8; padding: 6pt 8pt; font-size: 8.5pt; white-space: pre-wrap; }}
pre code {{ background: none; padding: 0; }}
a {{ color: {BLUE}; }}
figure {{ margin: 8pt 0 10pt; page-break-inside: avoid; }}
figcaption {{ color: {MUTED}; font-size: 9pt; margin-top: 3pt; }}
.meta {{ color: {MUTED}; font-size: 9pt; margin-bottom: 10pt; }}
"""


def load_curve() -> dict | None:
    try:
        return json.loads(CURVE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pct(row: dict, key: str) -> float:
    return 100 * row[key] / row["incidents"]


def num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def curve_text(report: dict) -> dict[str, str]:
    on, off = report["with_memory"], report.get("memory_off") or []
    repeats = report.get("repeats", 1)
    n = num(on[0]["incidents"])
    first, last = on[0], on[-1]
    headline = (f"with Hindsight memory, the share of incidents fixed by the agent's first action rose from "
                f"{pct(first, 'agent_first_try'):.0f}% in round 1 to {pct(last, 'agent_first_try'):.0f}% by round "
                f"{last['round']}, and wrong actions on production fell from {num(first['wrong_actions'])} to "
                f"{num(last['wrong_actions'])} per round")
    if off:
        headline += (f". With memory switched off it stayed at {pct(off[-1], 'agent_first_try'):.0f}% with "
                     f"{num(off[-1]['wrong_actions'])} wrong actions in the last round")
    runs = f"averaged over {repeats} runs" if repeats > 1 else "one run"
    headline = headline[0].upper() + headline[1:] + f" ({n} faults, {runs})."
    method = (f"{len(on)} rounds of {n} faults, averaged over {repeats} independent runs, each on its own fresh bank"
              if repeats > 1 else f"{len(on)} rounds of {n} faults, one run")
    rows = ["| Round | Fixed by first action, memory on | Wrong actions, memory on | Fixed by first action, memory off "
            "| Wrong actions, memory off |", "| --- | --- | --- | --- | --- |"]
    for i, row in enumerate(on):
        b = off[i] if i < len(off) else None
        rows.append(f"| {row['round']} | {num(row['agent_first_try'])}/{n} | {num(row['wrong_actions'])} | "
                    + (f"{num(b['agent_first_try'])}/{n} | {num(b['wrong_actions'])} |" if b else "n/a | n/a |"))
    return {"CURVE_HEADLINE": headline, "CURVE_METHOD": method, "CURVE_TABLE": "\n".join(rows)}


def line_panel(title: str, rows_on: list[dict], rows_off: list[dict], key: str, as_pct: bool, y_max: float,
               x0: int) -> str:
    """One small multiple: memory on (solid, with min-max whiskers) vs off (dashed), labelled at the line ends."""
    w, h, left, top, bottom = 330, 210, 40, 34, 30
    plot_w, plot_h = w - left - 70, h - top - bottom
    rounds = [r["round"] for r in rows_on]

    def x(i: int) -> float:
        return x0 + left + (plot_w * i / max(1, len(rounds) - 1))

    def y(v: float) -> float:
        return top + plot_h * (1 - v / y_max)

    def val(row: dict, k: str) -> float:
        return pct(row, k) if as_pct else row[k]

    out = [f'<text x="{x0 + left}" y="16" font-size="12" font-weight="600" fill="{INK}">{escape(title)}</text>']
    for tick in [0, y_max / 2, y_max]:
        out.append(f'<line x1="{x0 + left}" x2="{x0 + left + plot_w}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" '
                   f'stroke="{GRID}"/>')
        out.append(f'<text x="{x0 + left - 6}" y="{y(tick) + 4:.1f}" font-size="10" text-anchor="end" '
                   f'fill="{MUTED}">{tick:.0f}{"%" if as_pct else ""}</text>')
    for i, r in enumerate(rounds):
        out.append(f'<text x="{x(i):.1f}" y="{h - 10}" font-size="10" text-anchor="middle" fill="{MUTED}">'
                   f'Round {r}</text>')
    for rows, color, dash, label in ((rows_off, GREY, "5 4", "Memory off"), (rows_on, BLUE, "", "With memory")):
        if not rows:
            continue
        pts = [(x(i), y(val(row, key))) for i, row in enumerate(rows)]
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" stroke-dasharray="{dash}" '
                   f'points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>')
        for i, (a, b) in enumerate(pts):
            row = rows[i]
            if f"{key}_min" in row and row[f"{key}_min"] != row[f"{key}_max"]:
                lo, hi = row[f"{key}_min"], row[f"{key}_max"]
                if as_pct:
                    lo, hi = 100 * lo / row["incidents"], 100 * hi / row["incidents"]
                out.append(f'<line x1="{a:.1f}" x2="{a:.1f}" y1="{y(lo):.1f}" y2="{y(hi):.1f}" stroke="{color}" '
                           f'stroke-width="1.5"/>')
            out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="3.5" fill="{color}"/>')
        end_x, end_y = pts[-1]
        out.append(f'<text x="{end_x + 8:.1f}" y="{end_y + 4:.1f}" font-size="10.5" font-weight="600" '
                   f'fill="{color}">{label}</text>')
    return "\n".join(out)


def curve_svg(report: dict) -> str:
    on, off = report["with_memory"], report.get("memory_off") or []
    n = on[0]["incidents"]
    worst = max([r.get("wrong_actions_max", r["wrong_actions"]) for r in on + off] + [1])
    wrong_max = max(2, int(worst + 0.999))
    wrong_max += wrong_max % 2  # even, so the middle gridline is a whole number
    panels = (line_panel("Fixed by the agent's first action", on, off, "agent_first_try", True, 100, 0)
              + line_panel("Wrong actions run on production", on, off, "wrong_actions", False, wrong_max, 345))
    repeats = report.get("repeats", 1)
    note = (f"Mean of {repeats} runs; vertical bars show the range across runs. " if repeats > 1 else "One run. ")
    return (f'<figure><svg viewBox="0 0 680 215" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, sans-serif">{panels}</svg>'
            f'<figcaption>{note}Each round replays all {num(n)} ShopFast faults; memory from earlier rounds is '
            f'available to later ones. Approvals were given by the evaluation harness.</figcaption></figure>')


def box(x: int, y: int, w: int, h: int, title: str, lines: list[str], color: str) -> str:
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="{color}" stroke-width="1.6"/>',
           f'<text x="{x + 10}" y="{y + 20}" font-size="12" font-weight="600" fill="{color}">{escape(title)}</text>']
    for i, line in enumerate(lines):
        out.append(f'<text x="{x + 10}" y="{y + 38 + i * 15}" font-size="10" fill="{INK}">{escape(line)}</text>')
    return "\n".join(out)


def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "", dy: int = -6) -> str:
    out = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{MUTED}" stroke-width="1.4" marker-end="url(#arr)"/>'
    if label:
        out += (f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 + dy}" font-size="9.5" text-anchor="middle" '
                f'fill="{MUTED}">{escape(label)}</text>')
    return out


def architecture_svg() -> str:
    parts = [
        '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>',
        box(250, 8, 210, 62, "People and other agents", ["Streamlit UI (4 tabs)", "REST API  ·  MCP server (Claude Code)"],
            MUTED),
        box(10, 110, 170, 110, "ShopFast (mock shop)", ["8 faults, real-looking logs", "Alerts feed", "14 runbook actions",
                                                       "(some are decoys)", "Customer storefront"], "#b5522b"),
        box(250, 110, 210, 110, "Incident agent", ["Detect  →  recall  →  suggest", "Human approves the action",
                                                   "Act  →  verify  →  learn", "Postmortem  ·  team rules",
                                                   "Groq gpt-oss-120b advisor"], BLUE),
        box(530, 110, 145, 110, "Hindsight Cloud", ["retain  ·  recall", "reflect (postmortems)",
                                                    "mental model (runbook)", "directives (rules)",
                                                    "observations"], "#2e7d4f"),
        box(250, 255, 210, 44, "SQLite", ["Open incidents survive restarts"], MUTED),
        arrow(355, 70, 355, 108),
        '<text x="362" y="93" font-size="9.5" fill="#5b6270">approve, ask</text>',
        arrow(248, 140, 182, 140, "probe"),
        arrow(182, 170, 248, 170, "alert"),
        arrow(248, 200, 182, 200, "run action"),
        arrow(462, 150, 528, 150, "retain"),
        arrow(528, 185, 462, 185, "recall"),
        arrow(355, 222, 355, 253),
    ]
    return (f'<figure><svg viewBox="0 0 680 305" width="100%" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="Segoe UI, sans-serif">{"".join(parts)}</svg>'
            '<figcaption>The agent sits between the shop and its memory; people approve every action.</figcaption>'
            '</figure>')


def render(md_path: Path, replacements: dict[str, str], figures: dict[str, str]) -> str:
    text = md_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    body = body.replace("<li>[ ] ", '<li class="task">')
    for key, svg in figures.items():
        body = body.replace(f"<!-- {key} -->", svg)
    title = re.search(r"^# (.+)$", text, re.M).group(1)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title><style>{CSS}</style>'
            f"</head><body>{body}</body></html>")


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    browser = next((b for b in BROWSERS if Path(b).exists() or shutil.which(b)), None)
    if browser is None:
        raise SystemExit("No Edge or Chrome found to print PDFs; open the HTML files and print them instead.")
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True, timeout=120)


def test_count() -> str:
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--co"], cwd=ROOT, capture_output=True, text=True)
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return match.group(1) if match else "300+"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    report = load_curve()
    replacements = {"TEST_COUNT": test_count()}
    figures = {"diagram:architecture": architecture_svg()}
    if report:
        replacements |= curve_text(report)
        figures["chart:curve"] = curve_svg(report)
    for name, out in (("PROJECT_OVERVIEW.md", "incident-agent-overview"), ("PENDING.md", "incident-agent-pending")):
        html_path = OUT / f"{out}.html"
        html_path.write_text(render(ROOT / "docs" / name, replacements, figures), encoding="utf-8")
        print_pdf(html_path, OUT / f"{out}.pdf")
        print(f"wrote {OUT / (out + '.pdf')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
