"""Self-contained HTML rendering for profile reports.

Produces a single-file HTML document with inlined CSS — no external
assets, no CDN, no JavaScript. Suitable for emailing, archiving, or
dropping into a Slack thread for the data-governance ghost.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from typing import TYPE_CHECKING

from ..pii import confidence_band
from ..profile import ColumnProfile, ProfileReport

if TYPE_CHECKING:
    from ..llm import LLMResult
    from ..personas import Persona

__all__ = ["dumps"]


_CSS = """
*, *::before, *::after { box-sizing: border-box; }
html { color-scheme: light dark; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  margin: 0;
  padding: 2rem 1.5rem 4rem;
  background: #faf7ff;
  color: #1c1530;
  line-height: 1.5;
}
.wrap { max-width: 1100px; margin: 0 auto; }
header.banner {
  background: linear-gradient(135deg, #2b1055 0%, #7597de 100%);
  color: #fff;
  border-radius: 14px;
  padding: 1.75rem 2rem;
  margin-bottom: 2rem;
  box-shadow: 0 8px 30px rgba(43, 16, 85, 0.25);
}
header.banner h1 {
  margin: 0 0 .25rem;
  font-size: 1.65rem;
  letter-spacing: .01em;
}
header.banner .tagline { font-style: italic; opacity: .85; }
header.banner .greeting { margin-top: 1rem; opacity: .95; }
header.banner .greeting p { margin: .25rem 0; }
section.card {
  background: #fff;
  border: 1px solid #e6dff5;
  border-radius: 12px;
  padding: 1.25rem 1.5rem 1.5rem;
  margin-bottom: 1.5rem;
  box-shadow: 0 1px 2px rgba(43, 16, 85, 0.04);
}
section.card h2 {
  margin: 0 0 .25rem;
  font-size: 1.2rem;
  color: #4a1e8a;
}
section.card .intro { color: #5a4d77; font-style: italic; margin: 0 0 1rem; }
table.profile {
  width: 100%;
  border-collapse: collapse;
  font-size: .92rem;
}
table.profile th, table.profile td {
  padding: .45rem .6rem;
  border-bottom: 1px solid #ece5fa;
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}
table.profile th {
  background: #f4eefe;
  color: #4a1e8a;
  font-weight: 600;
  border-bottom: 1px solid #d9c9f5;
}
table.profile td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.profile tr:last-child td { border-bottom: none; }
.meta-grid { display: grid; grid-template-columns: max-content 1fr; gap: .4rem 1.25rem; }
.meta-grid .k { font-weight: 600; color: #4a1e8a; }
.band { display: inline-block; padding: .1rem .55rem; border-radius: 999px;
        font-size: .78rem; font-weight: 600; text-transform: uppercase;
        letter-spacing: .04em; }
.band-high   { background: #ffe1e1; color: #a4161a; }
.band-medium { background: #fff4d6; color: #8a5a00; }
.band-low    { background: #e5efff; color: #1f4a8a; }
.band-none   { background: #ececec; color: #555; }
.sev-high { color: #a4161a; font-weight: 600; }
.sev-warn { color: #8a5a00; font-weight: 600; }
.sev-info { color: #1f4a8a; font-weight: 600; }
.reading { white-space: pre-wrap; font-size: 1.02rem; line-height: 1.6; }
.reading + .reading-meta {
  margin-top: .75rem; color: #5a4d77; font-size: .85rem;
}
.null { color: #888; }
footer.foot {
  margin-top: 2rem; color: #6c5f8a; font-size: .8rem; text-align: center;
}
@media (prefers-color-scheme: dark) {
  body { background: #14101e; color: #ece7fa; }
  section.card { background: #1f1830; border-color: #2e2547; }
  section.card h2 { color: #c9b8ff; }
  section.card .intro { color: #b3a9cc; }
  table.profile th { background: #2a2143; color: #c9b8ff; border-bottom-color: #3a2f5c; }
  table.profile th, table.profile td { border-bottom-color: #2e2547; }
  .band-high   { background: #4a1216; color: #ffb3b8; }
  .band-medium { background: #4a3a05; color: #ffd97a; }
  .band-low    { background: #1a2742; color: #b8cdff; }
  .band-none   { background: #2a2540; color: #a89fbf; }
  footer.foot { color: #8b80a8; }
}
"""


def _e(value: object) -> str:
    """HTML-escape a value, with a placeholder for None."""
    if value is None:
        return '<span class="null">∅</span>'
    return _html.escape(str(value), quote=False)


def _e_attr(value: str) -> str:
    return _html.escape(value, quote=True)


def _humanize_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{n} B"


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e6 or (value != 0 and abs(value) < 1e-3):
        return f"{value:.3e}"
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _fmt_value(value: object, *, width: int = 60) -> str:
    if value is None:
        return '<span class="null">∅</span>'
    s = str(value)
    if len(s) > width:
        s = s[: width - 1] + "…"
    return _html.escape(s, quote=False)


def _fmt_top(col: ColumnProfile) -> str:
    if not col.top:
        return '<span class="null">—</span>'
    chunks = [f"{_fmt_value(t.value, width=24)} ×{t.count}" for t in col.top[:3]]
    return ", ".join(chunks)


def _banner(persona: Persona | None, *, title: str) -> str:
    if persona is None:
        return (
            f'<header class="banner"><h1>{_e(title)}</h1>'
            '<div class="tagline">schema-seance HTML report</div></header>'
        )
    greeting_html = "".join(
        f"<p>{_html.escape(line, quote=False)}</p>" for line in persona.greeting_lines
    )
    return (
        '<header class="banner">'
        f"<h1>{_html.escape(persona.emoji, quote=False)} "
        f"{_html.escape(persona.display_name, quote=False)} "
        f"— {_html.escape(title, quote=False)}</h1>"
        f'<div class="tagline">{_html.escape(persona.tagline, quote=False)}</div>'
        f'<div class="greeting">{greeting_html}</div>'
        "</header>"
    )


def _veil_card(report: ProfileReport) -> str:
    rows = [
        ("File", _e(report.path) if report.path else "—"),
        ("Rows", f"{report.rows:,}"),
        ("Columns", f"{report.cols:,}"),
        ("Size", _humanize_bytes(report.size_bytes)),
        ("Encoding", _e(report.encoding) if report.encoding else "—"),
    ]
    if report.sampled:
        rows.append(("Sampled", f"first {report.sample_size:,} rows"))
    items = "".join(f'<div class="k">{_e(k)}</div><div class="v">{v}</div>' for k, v in rows)
    return (
        '<section class="card">'
        "<h2>🕯 The Veil Parts</h2>"
        '<p class="intro">File metadata, as the spirits reveal it.</p>'
        f'<div class="meta-grid">{items}</div>'
        "</section>"
    )


def _spirits_card(report: ProfileReport) -> str:
    head = (
        "<thead><tr>"
        "<th>Column</th><th>Type</th><th>Null %</th><th>Distinct</th>"
        "<th>Min</th><th>Max</th><th>Mean</th><th>Stddev</th><th>Top</th>"
        "</tr></thead>"
    )
    body_rows = []
    for col in report.columns:
        body_rows.append(
            "<tr>"
            f"<td>{_e(col.name)}</td>"
            f"<td>{_e(col.dtype)}</td>"
            f'<td class="num">{col.null_pct:.2f}</td>'
            f'<td class="num">{col.distinct:,}</td>'
            f"<td>{_fmt_value(col.min, width=40)}</td>"
            f"<td>{_fmt_value(col.max, width=40)}</td>"
            f'<td class="num">{_fmt_number(col.mean)}</td>'
            f'<td class="num">{_fmt_number(col.stddev)}</td>'
            f"<td>{_fmt_top(col)}</td>"
            "</tr>"
        )
    body = "<tbody>" + "".join(body_rows) + "</tbody>"
    return (
        '<section class="card">'
        "<h2>👻 The Spirits Speak</h2>"
        '<p class="intro">Per-column statistics, drawn from the veil.</p>'
        f'<table class="profile">{head}{body}</table>'
        "</section>"
    )


def _pii_card(report: ProfileReport) -> str | None:
    rows = [(col, f) for col in report.columns for f in col.pii if f.confidence > 0]
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1].confidence)
    head = (
        "<thead><tr>"
        "<th>Column</th><th>Kind</th><th>Band</th>"
        "<th>Confidence</th><th>Match</th>"
        "</tr></thead>"
    )
    body_rows = []
    for col, finding in rows:
        band = confidence_band(finding.confidence)
        body_rows.append(
            "<tr>"
            f"<td>{_e(col.name)}</td>"
            f"<td>{_e(finding.kind)}</td>"
            f'<td><span class="band band-{_e_attr(band)}">{_e(band)}</span></td>'
            f'<td class="num">{finding.confidence:.2f}</td>'
            f'<td class="num">{finding.matched}/{finding.sampled}</td>'
            "</tr>"
        )
    body = "<tbody>" + "".join(body_rows) + "</tbody>"
    return (
        '<section class="card">'
        "<h2>🔮 Whispers of the Personal</h2>"
        '<p class="intro">Columns the spirits suspect of holding personal data.</p>'
        f'<table class="profile">{head}{body}</table>'
        "</section>"
    )


def _anomalies_card(report: ProfileReport) -> str | None:
    rows = [(col, a) for col in report.columns for a in col.anomalies]
    if not rows:
        return None
    severity_rank = {"high": 0, "warn": 1, "info": 2}
    rows.sort(key=lambda r: severity_rank.get(r[1].severity, 9))
    head = "<thead><tr><th>Column</th><th>Kind</th><th>Severity</th><th>Detail</th></tr></thead>"
    body_rows = []
    for col, anomaly in rows:
        sev_class = f"sev-{_e_attr(anomaly.severity)}"
        body_rows.append(
            "<tr>"
            f"<td>{_e(col.name)}</td>"
            f"<td>{_e(anomaly.kind)}</td>"
            f'<td class="{sev_class}">{_e(anomaly.severity)}</td>'
            f"<td>{_e(anomaly.detail)}</td>"
            "</tr>"
        )
    body = "<tbody>" + "".join(body_rows) + "</tbody>"
    return (
        '<section class="card">'
        "<h2>⚡ Restless Anomalies</h2>"
        '<p class="intro">What is suspicious, mixed, or restless in the data.</p>'
        f'<table class="profile">{head}{body}</table>'
        "</section>"
    )


def _reading_card(persona: Persona | None, reading: LLMResult) -> str:
    title = persona.reading_panel_title if persona is not None else "A Reading"
    bits = [f"model={_html.escape(reading.model, quote=False)}"]
    if reading.total_tokens is not None:
        bits.append(
            f"tokens={reading.total_tokens} "
            f"(prompt {reading.prompt_tokens}, completion {reading.completion_tokens})"
        )
    if reading.cost_usd is not None:
        bits.append(f"cost ≈ ${reading.cost_usd:.4f}")
    bits.append(f"in {reading.elapsed_seconds:.2f}s")
    meta = " · ".join(_html.escape(b, quote=False) for b in bits)
    return (
        '<section class="card">'
        f"<h2>{_html.escape(title, quote=False)}</h2>"
        '<p class="intro">A reading from the other side, as channelled by the LLM.</p>'
        f'<div class="reading">{_html.escape(reading.text, quote=False)}</div>'
        f'<div class="reading-meta">{meta}</div>'
        "</section>"
    )


def dumps(
    report: ProfileReport,
    *,
    persona: Persona | None = None,
    reading: LLMResult | None = None,
    title: str = "Seance Report",
) -> str:
    """Render *report* as a complete self-contained HTML document."""
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{_html.escape(title, quote=False)}</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append('</head><body><div class="wrap">')
    parts.append(_banner(persona, title=title))
    if reading is not None:
        parts.append(_reading_card(persona, reading))
    parts.append(_veil_card(report))
    parts.append(_spirits_card(report))
    pii = _pii_card(report)
    if pii is not None:
        parts.append(pii)
    anomalies = _anomalies_card(report)
    if anomalies is not None:
        parts.append(anomalies)
    generated = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f'<footer class="foot">Generated by schema-seance · {generated}</footer>')
    parts.append("</div></body></html>")
    return "".join(parts)
