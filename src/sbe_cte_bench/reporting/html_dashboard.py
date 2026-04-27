"""Self-contained HTML dashboard for a benchmark sweep.

Walks ``results/raw/`` for the latest record per (scenario, variant) and emits
a single HTML page with:

* A cross-scenario summary table (Mongo / Oracle / ratio / equivalence).
* Overall comparison charts (Chart.js, loaded from CDN — only external dep).
* Per-scenario sections; each variant gets a tabbed pane with:
    - Mongo aggregation pipeline (pretty-printed JSON)
    - Mongo explain output (executionStats verbosity)
    - Oracle SQL
    - Oracle execution plan (DBMS_XPLAN.DISPLAY('ALL'))
    - Link to the active SQL Monitor report HTML
    - Per-variant timing chart (Mongo vs Oracle median + p95)

The output is one self-contained HTML file. SQL Monitor reports remain
separate files under ``results/sql_monitor/`` and are linked relatively.
"""

from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_records(raw_dir: Path) -> list[dict[str, Any]]:
    """Return the latest record per (scenario, variant_label)."""
    latest: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
    for f in raw_dir.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        scenario = r.get("scenario", "?")
        variant = r.get("variant") or {}
        label = variant.get("label", "default") if isinstance(variant, dict) else str(variant)
        key = (scenario, label)
        mtime = f.stat().st_mtime
        prev = latest.get(key)
        if prev is None or prev[0] < mtime:
            latest[key] = (mtime, r)
    return [r for _, r in sorted(latest.values(), key=lambda x: (x[1].get("scenario", ""), x[1].get("variant", {}).get("label", "")))]


def _scenario_groups(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[r.get("scenario", "?")].append(r)
    for scenario, lst in groups.items():
        lst.sort(key=lambda r: (r.get("variant", {}).get("label") or "default"))
    return dict(sorted(groups.items()))


def _ratio(record: dict[str, Any]) -> float:
    m = (record.get("mongo") or {}).get("median_ms", 0.0)
    o = (record.get("oracle") or {}).get("median_ms", 0.0)
    return (m / o) if o else 0.0


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)


def _pretty_json(obj: Any, indent: int = 2) -> str:
    """JSON pretty-print, escaped for safe HTML embedding."""
    try:
        s = json.dumps(obj, indent=indent, default=str, sort_keys=False)
    except Exception:
        s = repr(obj)
    return html.escape(s)


def _summary_row(record: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Extract the per-record fields the summary table needs."""
    m = record.get("mongo") or {}
    o = record.get("oracle") or {}
    eq = record.get("equivalence") or {}
    pred = record.get("prediction") or {}
    sm_path = o.get("sql_monitor_path")
    sm_link = ""
    if sm_path:
        # Resolve to a path relative to the dashboard output file's parent.
        try:
            abs_sm = Path(sm_path).resolve()
            rel = os.path.relpath(abs_sm, output_dir.resolve())
            sm_link = rel
        except Exception:
            sm_link = sm_path
    ratio = _ratio(record)
    return {
        "scenario": record.get("scenario", "?"),
        "variant": (record.get("variant") or {}).get("label", "default"),
        "mongo_median_ms": m.get("median_ms", 0.0),
        "mongo_p95_ms": m.get("p95_ms", 0.0),
        "oracle_median_ms": o.get("median_ms", 0.0),
        "oracle_p95_ms": o.get("p95_ms", 0.0),
        "ratio": ratio,
        "match": bool(eq.get("match")),
        "row_count_mongo": eq.get("row_count_mongo", 0),
        "row_count_oracle": eq.get("row_count_oracle", 0),
        "predicted_pass": bool(pred.get("pass") or pred.get("pass_")),
        "predicted_claim": pred.get("claim", ""),
        "sql_monitor_link": sm_link,
    }


def _tab_html(*, key: str, label: str, content: str) -> tuple[str, str]:
    """Return (radio-button + label, content div) for a single tab."""
    return (
        f'<input type="radio" name="{key}-tabs" id="{key}-tab" />'
        f'<label for="{key}-tab">{html.escape(label)}</label>',
        f'<div class="tab-content" id="{key}-content">{content}</div>',
    )


def _format_oracle_plan(plan_block: dict[str, Any]) -> str:
    """Render the Oracle xplan block as preformatted text."""
    if not plan_block:
        return "<p><em>no plan captured</em></p>"
    text = plan_block.get("plan_text") or plan_block.get("text") or ""
    if not text:
        # Fall back to dumping the parsed structure
        return f"<pre>{_pretty_json(plan_block)}</pre>"
    return f"<pre>{html.escape(text)}</pre>"


def _format_mongo_explain(explain_block: dict[str, Any]) -> str:
    """Render the Mongo explain block. Prefer the raw blob if present."""
    if not explain_block:
        return "<p><em>no explain captured</em></p>"
    body = explain_block.get("raw") if isinstance(explain_block, dict) else explain_block
    if not body:
        body = explain_block
    return f"<pre>{_pretty_json(body)}</pre>"


def _variant_pane(record: dict[str, Any], output_dir: Path, idx: int) -> str:
    """Render one variant as a tabbed pane with five sub-tabs."""
    scenario = record.get("scenario", "?")
    variant = (record.get("variant") or {}).get("label", "default")
    pane_key = f"{_slug(scenario)}__{_slug(variant)}__{idx}"

    m = record.get("mongo") or {}
    o = record.get("oracle") or {}
    eq = record.get("equivalence") or {}
    sm_path = o.get("sql_monitor_path")

    sm_link_html = ""
    if sm_path:
        try:
            abs_sm = Path(sm_path).resolve()
            rel = os.path.relpath(abs_sm, output_dir.resolve())
        except Exception:
            rel = sm_path
        sm_link_html = (
            f'<p><a href="{html.escape(rel)}" target="_blank" rel="noopener">'
            f"Open active SQL Monitor report ↗</a></p>"
            f'<p class="muted">Active reports are interactive Oracle diagnostic '
            f"pages; they connect back to Oracle's online infrastructure for "
            "live drill-downs into per-row-source statistics.</p>"
        )
    else:
        sm_link_html = "<p><em>no SQL Monitor report captured for this run</em></p>"

    pipeline = m.get("pipeline") or []
    explain = m.get("explain") or {}
    sql = o.get("sql") or ""
    plan = o.get("plan") or {}

    # Header summary
    ratio = _ratio(record)
    eq_text = "MATCH" if eq.get("match") else "MISMATCH"
    eq_class = "ok" if eq.get("match") else "bad"
    header = (
        f'<div class="variant-header">'
        f"<h3>{html.escape(variant)}</h3>"
        f'<div class="metrics">'
        f'<span class="metric">Mongo median <strong>{m.get("median_ms", 0.0):.1f} ms</strong></span>'
        f'<span class="metric">Oracle median <strong>{o.get("median_ms", 0.0):.1f} ms</strong></span>'
        f'<span class="metric ratio">ratio <strong>{ratio:.2f}×</strong></span>'
        f'<span class="metric eq {eq_class}">{eq_text}</span>'
        f'<span class="metric">{eq.get("row_count_mongo", 0)} / {eq.get("row_count_oracle", 0)} rows</span>'
        f"</div></div>"
    )

    # Tabs (CSS-only via radio buttons)
    tabs = [
        ("pipeline", "Mongo Pipeline", f"<pre>{_pretty_json(pipeline)}</pre>"),
        ("explain", "Mongo Explain", _format_mongo_explain(explain)),
        ("sql", "Oracle SQL", f"<pre>{html.escape(sql)}</pre>"),
        ("plan", "Oracle Plan", _format_oracle_plan(plan)),
        ("monitor", "SQL Monitor", sm_link_html),
    ]
    inputs_labels = []
    contents = []
    for i, (slug, label, content) in enumerate(tabs):
        tid = f"{pane_key}__{slug}"
        checked = " checked" if i == 0 else ""
        inputs_labels.append(
            f'<input type="radio" name="{pane_key}" id="{tid}"{checked} />'
            f'<label for="{tid}">{html.escape(label)}</label>'
        )
        contents.append(f'<div class="tab-content" data-for="{tid}">{content}</div>')

    return (
        '<section class="variant-pane">'
        f"{header}"
        '<div class="tabs">'
        f'{"".join(inputs_labels)}'
        '<div class="tab-bodies">'
        f'{"".join(contents)}'
        "</div></div></section>"
    )


def _scenario_section(scenario_id: str, records: list[dict[str, Any]], output_dir: Path) -> str:
    if not records:
        return ""
    title = records[0].get("scenario_title", scenario_id)
    panes = "\n".join(_variant_pane(r, output_dir, i) for i, r in enumerate(records))
    rows_html = []
    for i, r in enumerate(records):
        m = r.get("mongo") or {}
        o = r.get("oracle") or {}
        eq = r.get("equivalence") or {}
        v = (r.get("variant") or {}).get("label", "default")
        ratio = _ratio(r)
        eq_class = "ok" if eq.get("match") else "bad"
        rows_html.append(
            f"<tr><td>{html.escape(v)}</td>"
            f'<td class="num">{m.get("median_ms", 0.0):.1f}</td>'
            f'<td class="num">{o.get("median_ms", 0.0):.1f}</td>'
            f'<td class="num ratio"><strong>{ratio:.2f}×</strong></td>'
            f'<td class="{eq_class}">{"MATCH" if eq.get("match") else "MISMATCH"}</td>'
            "</tr>"
        )
    table = (
        '<table class="variant-table">'
        "<thead><tr><th>Variant</th><th>Mongo (ms)</th><th>Oracle (ms)</th>"
        "<th>Ratio</th><th>Equiv.</th></tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody></table>'
    )
    return (
        f'<section class="scenario" id="{scenario_id}">'
        f'<h2>{html.escape(scenario_id)} — {html.escape(title)}</h2>'
        f"{table}"
        f"{panes}"
        "</section>"
    )


def _build_overall_chart_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Chart.js dataset payloads (labels + Mongo/Oracle median + ratio)."""
    labels: list[str] = []
    mongo: list[float] = []
    oracle: list[float] = []
    ratios: list[float] = []
    for r in rows:
        labels.append(f"{r['scenario']} · {r['variant']}")
        mongo.append(round(r["mongo_median_ms"], 2))
        oracle.append(round(r["oracle_median_ms"], 2))
        ratios.append(round(r["ratio"], 3))
    return {"labels": labels, "mongo": mongo, "oracle": oracle, "ratios": ratios}


_CSS = """\
:root {
  --ink: #1a1a1a; --bg: #fafafa; --bg-2: #fff; --line: #e0e0e0;
  --accent: #c74634; --accent-2: #1f6f8b; --ok: #4caf50; --bad: #d32f2f;
  --muted: #777;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       color: var(--ink); background: var(--bg); line-height: 1.5; }
header.page { background: linear-gradient(135deg, #1f2937 0%, #111827 100%);
              color: #fafafa; padding: 32px 48px; }
header.page h1 { margin: 0; font-size: 28px; }
header.page p { margin: 6px 0 0; opacity: 0.8; }
main { max-width: 1280px; margin: 0 auto; padding: 32px 48px; }
section.summary { background: var(--bg-2); border: 1px solid var(--line);
                  border-radius: 8px; padding: 24px; margin-bottom: 32px; }
section.summary h2 { margin-top: 0; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px; margin: 16px 0; }
.kpi { background: #f5f7fa; padding: 16px; border-radius: 6px; text-align: center; }
.kpi .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.kpi .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }
th { text-align: left; background: #f0f0f0; padding: 8px 12px; border-bottom: 2px solid var(--line); font-weight: 600; }
td { padding: 8px 12px; border-bottom: 1px solid var(--line); }
td.num { font-variant-numeric: tabular-nums; text-align: right; }
td.ratio { color: var(--accent); }
td.ok, span.ok { color: var(--ok); font-weight: 600; }
td.bad, span.bad { color: var(--bad); font-weight: 600; }
section.scenario { background: var(--bg-2); border: 1px solid var(--line);
                   border-radius: 8px; padding: 24px; margin-bottom: 24px; }
section.scenario h2 { margin-top: 0; color: var(--accent-2); }
.variant-table { margin-bottom: 24px; }
.variant-table td.ratio strong { color: var(--accent); }
.variant-pane { border-top: 1px solid var(--line); padding-top: 20px; margin-top: 20px; }
.variant-header { display: flex; justify-content: space-between; align-items: baseline;
                  flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.variant-header h3 { margin: 0; font-family: ui-monospace, "SF Mono", Menlo, monospace;
                     font-size: 16px; color: var(--accent-2); }
.metrics { display: flex; gap: 16px; flex-wrap: wrap; }
.metric { font-size: 13px; color: var(--muted); }
.metric strong { color: var(--ink); margin-left: 4px; }
.metric.ratio strong { color: var(--accent); }
.metric.eq { padding: 2px 8px; border-radius: 3px; }
.metric.eq.ok { background: #e8f5e9; color: var(--ok); }
.metric.eq.bad { background: #ffebee; color: var(--bad); }
.tabs { display: flex; flex-wrap: wrap; gap: 0; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.tabs > input[type="radio"] { display: none; }
.tabs > label { padding: 10px 18px; cursor: pointer; background: #f5f7fa;
                border-right: 1px solid var(--line); font-size: 13px; flex: 0 0 auto; }
.tabs > label:hover { background: #eaeef2; }
.tabs > input[type="radio"]:checked + label { background: var(--bg-2); font-weight: 600; color: var(--accent-2); }
.tab-bodies { width: 100%; flex: 1 1 100%; min-height: 200px; padding: 16px; background: var(--bg-2); }
.tab-content { display: none; }
.tabs > input[type="radio"]:nth-of-type(1):checked ~ .tab-bodies > .tab-content:nth-child(1),
.tabs > input[type="radio"]:nth-of-type(2):checked ~ .tab-bodies > .tab-content:nth-child(2),
.tabs > input[type="radio"]:nth-of-type(3):checked ~ .tab-bodies > .tab-content:nth-child(3),
.tabs > input[type="radio"]:nth-of-type(4):checked ~ .tab-bodies > .tab-content:nth-child(4),
.tabs > input[type="radio"]:nth-of-type(5):checked ~ .tab-bodies > .tab-content:nth-child(5) { display: block; }
pre { background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 4px;
      font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
      overflow-x: auto; line-height: 1.4; max-height: 600px; }
.muted { color: var(--muted); font-size: 12px; }
nav.scenario-nav { background: #f0f0f0; padding: 12px 24px; border-radius: 6px;
                   margin-bottom: 24px; font-size: 13px; }
nav.scenario-nav a { color: var(--accent-2); text-decoration: none; margin-right: 16px; font-weight: 500; }
nav.scenario-nav a:hover { text-decoration: underline; }
.chart-container { background: var(--bg-2); border: 1px solid var(--line);
                   border-radius: 6px; padding: 16px; margin: 16px 0; }
"""


_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>sbe-cte-bench dashboard — {scale} run {timestamp}</title>
<style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<header class="page">
  <h1>sbe-cte-bench — {scale} sweep dashboard</h1>
  <p>MongoDB (cgroup-capped to ADB envelope) vs Oracle Autonomous Database. Generated {timestamp}.</p>
</header>
<main>
  <section class="summary">
    <h2>Headline numbers</h2>
    <div class="kpi-grid">
      <div class="kpi"><div class="label">runs</div><div class="value">{n_runs}</div></div>
      <div class="kpi"><div class="label">equivalence MATCH</div><div class="value">{n_match}</div></div>
      <div class="kpi"><div class="label">Oracle wins (≥1.5×)</div><div class="value">{n_oracle_wins}</div></div>
      <div class="kpi"><div class="label">peak Oracle ratio</div><div class="value">{peak_ratio:.1f}×</div></div>
    </div>
    <h3>All variants</h3>
    <table>
      <thead><tr>
        <th>Scenario</th><th>Variant</th><th>Mongo (ms)</th><th>Oracle (ms)</th>
        <th>Ratio</th><th>Equiv.</th><th>SQL Monitor</th>
      </tr></thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </section>

  <section class="summary">
    <h2>Comparison chart</h2>
    <div class="chart-container">
      <canvas id="medianChart" height="80"></canvas>
    </div>
    <div class="chart-container">
      <canvas id="ratioChart" height="80"></canvas>
    </div>
  </section>

  <nav class="scenario-nav">{scenario_nav}</nav>

  {scenario_sections}
</main>

<script>
const CHART_DATA = {chart_data_json};

new Chart(document.getElementById('medianChart'), {{
  type: 'bar',
  data: {{
    labels: CHART_DATA.labels,
    datasets: [
      {{ label: 'Mongo median (ms)', data: CHART_DATA.mongo, backgroundColor: '#13aa52' }},
      {{ label: 'Oracle median (ms)', data: CHART_DATA.oracle, backgroundColor: '#c74634' }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ type: 'logarithmic', title: {{ display: true, text: 'median ms (log scale)' }} }} }},
    plugins: {{ title: {{ display: true, text: 'Median latency by variant (log scale)' }} }}
  }}
}});

new Chart(document.getElementById('ratioChart'), {{
  type: 'bar',
  data: {{
    labels: CHART_DATA.labels,
    datasets: [{{
      label: 'Mongo / Oracle ratio',
      data: CHART_DATA.ratios,
      backgroundColor: CHART_DATA.ratios.map(r => r >= 1.5 ? '#c74634' : (r <= 0.67 ? '#13aa52' : '#888')),
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ title: {{ display: true, text: 'mongo_ms / oracle_ms (>1 = Oracle wins)' }} }} }},
    plugins: {{
      title: {{ display: true, text: 'Architectural advantage by variant' }},
      annotation: {{ annotations: {{ line1: {{ type: 'line', yMin: 1, yMax: 1, borderColor: '#000', borderWidth: 1 }} }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def render_dashboard(raw_dir: Path, output: Path, scale_factor: str = "SF1") -> None:
    """Walk ``raw_dir``, build the dashboard HTML, write to ``output``."""
    output_dir = output.parent
    records = _load_records(raw_dir)
    if not records:
        output.write_text(
            "<!doctype html><html><body><h1>No records found</h1></body></html>",
            encoding="utf-8",
        )
        return

    summary_rows_data = [_summary_row(r, output_dir) for r in records]
    n_runs = len(summary_rows_data)
    n_match = sum(1 for s in summary_rows_data if s["match"])
    n_oracle_wins = sum(1 for s in summary_rows_data if s["ratio"] >= 1.5)
    peak_ratio = max((s["ratio"] for s in summary_rows_data), default=0.0)

    summary_rows_html_parts: list[str] = []
    for s in summary_rows_data:
        eq_class = "ok" if s["match"] else "bad"
        sm_cell = (
            f'<a href="{html.escape(s["sql_monitor_link"])}" target="_blank" rel="noopener">view</a>'
            if s["sql_monitor_link"]
            else '<span class="muted">—</span>'
        )
        summary_rows_html_parts.append(
            "<tr>"
            f'<td><a href="#{s["scenario"]}">{html.escape(s["scenario"])}</a></td>'
            f"<td>{html.escape(s['variant'])}</td>"
            f"<td class='num'>{s['mongo_median_ms']:.1f}</td>"
            f"<td class='num'>{s['oracle_median_ms']:.1f}</td>"
            f"<td class='num ratio'><strong>{s['ratio']:.2f}×</strong></td>"
            f"<td class='{eq_class}'>{'MATCH' if s['match'] else 'MISMATCH'}</td>"
            f"<td>{sm_cell}</td>"
            "</tr>"
        )

    groups = _scenario_groups(records)
    scenario_nav = " ".join(
        f'<a href="#{sid}">{html.escape(sid)}</a>' for sid in groups
    )
    scenario_sections = "\n".join(
        _scenario_section(sid, recs, output_dir) for sid, recs in groups.items()
    )

    chart_payload = _build_overall_chart_data(summary_rows_data)
    chart_data_json = json.dumps(chart_payload)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        _TEMPLATE.format(
            css=_CSS,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            scale=html.escape(scale_factor),
            n_runs=n_runs,
            n_match=n_match,
            n_oracle_wins=n_oracle_wins,
            peak_ratio=peak_ratio,
            summary_rows="".join(summary_rows_html_parts),
            scenario_nav=scenario_nav,
            scenario_sections=scenario_sections,
            chart_data_json=chart_data_json,
        ),
        encoding="utf-8",
    )
