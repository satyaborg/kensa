"""Report formatters: terminal (Rich), markdown, JSON, HTML."""

from __future__ import annotations

import html
import io
import json

from kensa.models import Result, ResultStatus


def _aggregate_stats(results: list[Result]) -> str:
    """Build a dim stats line from traced results. Omits zero-value items."""
    traces = [r.trace for r in results if r.trace]
    if not traces:
        return ""
    total_cost = sum(t.cost_usd for t in traces)
    total_tokens = sum(t.total_tokens for t in traces)
    total_llm = sum(t.llm_calls for t in traces)
    total_tool = sum(t.tool_calls for t in traces)
    total_dur = sum(t.duration_seconds for t in traces)
    parts: list[str] = []
    if total_cost:
        parts.append(f"${total_cost:.4f}")
    if total_tokens:
        parts.append(f"{total_tokens:,} tokens")
    if total_llm:
        parts.append(f"{total_llm} llm calls")
    if total_tool:
        parts.append(f"{total_tool} tool calls")
    parts.append(f"{total_dur:.1f}s")
    return " · ".join(parts)


def format_terminal(results: list[Result], verbose: bool = False) -> str:
    """Format results for terminal output using Rich markup."""
    try:
        from rich.console import Console

        from kensa.styles import build_results_table, summary_line

        console = Console(record=True, width=120 if verbose else 100, file=io.StringIO())

        console.print()
        console.print(f"[bold]kensa[/bold]  {summary_line(results)}", highlight=False)
        stats = _aggregate_stats(results)
        if stats:
            console.print(f"[dim]{stats}[/dim]", highlight=False)
        console.print()
        console.print(build_results_table(results))

        if verbose:
            for r in results:
                console.print(f"\n[bold]{r.scenario_id}[/bold]", highlight=False)
                if r.trace:
                    console.print(
                        f"  [dim]{r.trace.llm_calls} LLM calls, "
                        f"{r.trace.tool_calls} tool calls, "
                        f"{r.trace.total_tokens} tokens, "
                        f"${r.trace.cost_usd:.4f}, "
                        f"{r.trace.duration_seconds}s[/dim]",
                        highlight=False,
                    )
                if r.expected is not None:
                    console.print(f"  [dim]expected: {r.expected}[/dim]", highlight=False)
                for c in r.check_results:
                    tag = "[green]pass[/green]" if c.passed else "[red]fail[/red]"
                    console.print(f"  {tag}  {c.check}: {c.detail}", highlight=False)
                if r.judge_result:
                    if r.judge_result.verdict == ResultStatus.UNCERTAIN:
                        tag = "[blue]uncertain[/blue]"
                    elif r.judge_result.passed:
                        tag = "[green]pass[/green]"
                    else:
                        tag = "[red]fail[/red]"
                    console.print(f"  {tag}  judge: {r.judge_result.reasoning}", highlight=False)
                if r.error:
                    console.print(f"  [yellow]error: {r.error}[/yellow]", highlight=False)

        console.print()
        return console.export_text()

    except ImportError:
        return format_json(results)


def format_markdown(results: list[Result]) -> str:
    """Format results as markdown (for CI comments)."""
    lines: list[str] = []

    passed = sum(1 for r in results if r.status == ResultStatus.PASS)
    uncertain = sum(1 for r in results if r.status == ResultStatus.UNCERTAIN)
    total = len(results)

    header = f"## kensa results: {passed}/{total} passed"
    if uncertain:
        header += f", {uncertain} uncertain"
    lines.append(f"{header}\n")

    lines.append("| Scenario | Status | Checks | Judge | Details |")
    lines.append("|----------|--------|--------|-------|---------|")

    for r in results:
        status_label = r.status.value

        if r.check_results:
            check_pass = sum(1 for c in r.check_results if c.passed)
            checks = f"{check_pass}/{len(r.check_results)}"
        else:
            checks = "-"

        judge = "-"
        if r.judge_result:
            if r.judge_result.verdict:
                judge = r.judge_result.verdict.value
            else:
                judge = "pass" if r.judge_result.passed else "fail"

        detail = ""
        for c in r.check_results:
            if not c.passed:
                detail = f"`{c.check}`: {c.detail}"
                break
        if not detail and r.judge_result and not r.judge_result.passed:
            detail = r.judge_result.reasoning[:100]
        if not detail and r.error:
            detail = r.error[:100]

        lines.append(f"| {r.scenario_id} | {status_label} | {checks} | {judge} | {detail} |")

    failures = [r for r in results if r.status != ResultStatus.PASS]
    if failures:
        lines.append("\n### Failures\n")
        for r in failures:
            lines.append(f"#### {r.scenario_id}\n")
            if r.expected is not None:
                lines.append(f"- **Expected**: {r.expected}")
            lines.extend(f"- **{c.check}**: {c.detail}" for c in r.check_results if not c.passed)
            if r.judge_result and not r.judge_result.passed:
                lines.append(f"- **Judge**: {r.judge_result.reasoning}")
            if r.error:
                lines.append(f"- **Error**: {r.error}")
            if r.trace:
                lines.append(
                    f"- Trace: {r.trace.llm_calls} LLM calls, "
                    f"{r.trace.tool_calls} tool calls, "
                    f"${r.trace.cost_usd:.4f}, {r.trace.duration_seconds}s"
                )
            lines.append("")

    return "\n".join(lines)


def format_json(results: list[Result]) -> str:
    """Format results as JSON."""
    return json.dumps(
        [r.model_dump(mode="json") for r in results],
        indent=2,
    )


def format_html(results: list[Result]) -> str:
    """Format results as a self-contained interactive HTML dashboard."""
    from collections import Counter

    status_counts: Counter[ResultStatus] = Counter(r.status for r in results)
    passed = status_counts[ResultStatus.PASS]
    failed = status_counts[ResultStatus.FAIL]
    errors = status_counts[ResultStatus.ERROR]
    uncertain = status_counts[ResultStatus.UNCERTAIN]
    total = len(results)
    pass_pct = (passed / total * 100) if total else 0

    traces = [r.trace for r in results if r.trace]
    total_tokens = sum(t.total_tokens for t in traces)
    total_cost = sum(t.cost_usd for t in traces)
    total_llm = sum(t.llm_calls for t in traces)
    total_tool = sum(t.tool_calls for t in traces)
    total_dur = sum(t.duration_seconds for t in traces)

    id_counts: Counter[str] = Counter()
    id_seen: Counter[str] = Counter()
    for r in results:
        id_counts[r.scenario_id] += 1
    display_ids: list[str] = []
    for r in results:
        if id_counts[r.scenario_id] > 1:
            id_seen[r.scenario_id] += 1
            display_ids.append(f"{r.scenario_id} #{id_seen[r.scenario_id]}")
        else:
            display_ids.append(r.scenario_id)

    rows = ""
    col_count = 7
    for i, r in enumerate(results):
        esc_id = html.escape(display_ids[i])
        status_class = r.status.value
        status_label = r.status.value.upper()

        checks_str = "-"
        if r.check_results:
            check_pass = sum(1 for c in r.check_results if c.passed)
            checks_str = f"{check_pass}/{len(r.check_results)}"

        judge_str = "-"
        if r.judge_result:
            if r.judge_result.verdict:
                judge_str = r.judge_result.verdict.value
            else:
                judge_str = "pass" if r.judge_result.passed else "fail"

        cost_str = "-"
        dur_str = "-"
        if r.trace:
            cost_str = f"${r.trace.cost_usd:.4f}" if r.trace.cost_usd > 0 else "-"
            dur_str = f"{r.trace.duration_seconds:.1f}s"

        detail_str = ""
        if r.status != ResultStatus.PASS:
            for c in r.check_results:
                if not c.passed:
                    detail_str = html.escape(f"[{c.check}] {c.detail}")
                    break
            if not detail_str and r.judge_result and not r.judge_result.passed:
                detail_str = html.escape(r.judge_result.reasoning[:120])
            if not detail_str and r.error:
                last_line = next(
                    (ln for ln in reversed(r.error.splitlines()) if ln.strip()), r.error
                )
                detail_str = html.escape(last_line[:120])

        detail_td = f'<td class="detail-cell">{detail_str}</td>' if detail_str else "<td></td>"

        has_detail = bool(
            r.input is not None or r.check_results or r.judge_result or r.trace or r.error
        )
        expandable = ' class="expandable"' if has_detail else ""

        rows += (
            f'<tr data-status="{status_class}"{expandable}>'
            f"<td>{esc_id}</td>"
            f'<td><span class="badge {status_class}">{status_label}</span></td>'
            f"<td>{checks_str}</td>"
            f"<td>{judge_str}</td>"
            f'<td class="num">{cost_str}</td>'
            f'<td class="num">{dur_str}</td>'
            f"{detail_td}"
            f"</tr>\n"
        )

        if has_detail:
            panel = '<div class="expand-panel">'

            if r.input is not None:
                if isinstance(r.input, dict):
                    input_display = json.dumps(r.input, ensure_ascii=False)
                else:
                    input_display = str(r.input)
                panel += (
                    '<div class="panel-section">'
                    '<div class="panel-label">Input</div>'
                    f'<pre class="input-pre">{html.escape(input_display)}</pre>'
                    "</div>"
                )

            if r.expected is not None:
                panel += (
                    '<div class="panel-section">'
                    '<div class="panel-label">Expected</div>'
                    f'<pre class="input-pre">{html.escape(r.expected)}</pre>'
                    "</div>"
                )

            if r.check_results:
                panel += (
                    '<div class="panel-section">'
                    '<div class="panel-label">Checks</div>'
                    '<table class="checks-table">'
                )
                for c in r.check_results:
                    dot = "dot-pass" if c.passed else "dot-fail"
                    panel += (
                        f'<tr><td><span class="{dot}"></span></td>'
                        f'<td class="check-name">'
                        f"{html.escape(c.check)}</td>"
                        f"<td>{html.escape(c.detail)}</td></tr>"
                    )
                panel += "</table></div>"

            if r.judge_result:
                verdict_cls = "judge-pass" if r.judge_result.passed else "judge-fail"
                panel += (
                    '<div class="panel-section">'
                    '<div class="panel-label">Judge</div>'
                    f'<div class="{verdict_cls}">'
                    f"{html.escape(r.judge_result.reasoning)}</div>"
                )
                if r.judge_result.evidence:
                    panel += '<ul class="evidence">'
                    for ev in r.judge_result.evidence:
                        panel += f"<li>{html.escape(ev)}</li>"
                    panel += "</ul>"
                panel += "</div>"

            if r.trace:
                panel += (
                    '<div class="panel-section">'
                    '<div class="panel-label">Trace</div>'
                    '<div class="trace-grid">'
                    f'<div><span class="trace-val">{r.trace.llm_calls}</span>'
                    f' <span class="trace-key">LLM calls</span></div>'
                    f'<div><span class="trace-val">{r.trace.tool_calls}</span>'
                    f' <span class="trace-key">tool calls</span></div>'
                    f'<div><span class="trace-val">'
                    f"{r.trace.total_tokens:,}</span>"
                    f' <span class="trace-key">tokens</span></div>'
                    f'<div><span class="trace-val">'
                    f"${r.trace.cost_usd:.4f}</span>"
                    f' <span class="trace-key">cost</span></div>'
                    f'<div><span class="trace-val">'
                    f"{r.trace.duration_seconds:.1f}s</span>"
                    f' <span class="trace-key">duration</span></div>'
                    f"</div></div>"
                )

            if r.error:
                is_long = r.error.count("\n") >= 5
                if is_long:
                    label = (
                        f'<div class="panel-label collapsible" data-target="err-{i}">'
                        f'Error <span class="caret">&#9654;</span></div>'
                    )
                    pre_cls = f' class="error-pre collapsed" id="err-{i}"'
                else:
                    label = '<div class="panel-label">Error</div>'
                    pre_cls = ' class="error-pre"'
                panel += (
                    f'<div class="panel-section">{label}'
                    f"<pre{pre_cls}>{html.escape(r.error)}</pre></div>"
                )

            panel += "</div>"
            rows += (
                f'<tr class="detail-row hidden" data-status="{status_class}">'
                f'<td colspan="{col_count}">{panel}</td></tr>\n'
            )

    radius = 40
    stroke = 8
    circumference = 2 * 3.14159265 * radius
    pass_arc = circumference * pass_pct / 100
    donut_color = "#22c55e" if pass_pct == 100 else "#ef4444" if pass_pct < 50 else "#eab308"
    donut = (
        f'<svg class="donut" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="{radius}" fill="none" '
        f'stroke="rgba(255,255,255,0.08)" stroke-width="{stroke}"/>'
        f'<circle cx="50" cy="50" r="{radius}" fill="none" '
        f'stroke="{donut_color}" stroke-width="{stroke}" '
        f'stroke-dasharray="{pass_arc:.1f} {circumference:.1f}" '
        f'stroke-dashoffset="0"'
        f' class="donut-arc" transform="rotate(-90 50 50)"/>'
        f'<text x="50" y="50" text-anchor="middle" dominant-baseline="central" '
        f'class="donut-pct">{pass_pct:.0f}%</text>'
        f"</svg>"
    )

    def _stat(val: str, label: str, cls: str = "", filt: str = "") -> str:
        cv = f' class="stat-value {cls}"' if cls else ' class="stat-value"'
        data = f' data-filter="{filt}"' if filt else ""
        clickable = " clickable" if filt else ""
        return (
            f'<div class="stat{clickable}"{data}>'
            f"<div{cv}>{val}</div>"
            f'<div class="stat-label">{label}</div></div>'
        )

    stat_items: list[str] = []
    stat_items.append(_stat(f"{passed}", "Passed", "green", "pass"))
    if failed:
        stat_items.append(_stat(str(failed), "Failed", "red", "fail"))
    if uncertain:
        stat_items.append(_stat(str(uncertain), "Uncertain", "blue", "uncertain"))
    if errors:
        stat_items.append(_stat(str(errors), "Errors", "yellow", "error"))
    stat_items.append(_stat(f"${total_cost:.4f}", "Cost"))
    stat_items.append(_stat(f"{total_tokens:,}", "Tokens"))
    stat_items.append(_stat(f"{total_dur:.1f}s", "Duration"))
    if total_llm:
        stat_items.append(_stat(str(total_llm), "LLM Calls"))
    if total_tool:
        stat_items.append(_stat(str(total_tool), "Tool Calls"))
    stats_html = "\n".join(stat_items)

    font_url = (
        "https://fonts.googleapis.com/css2"
        "?family=Geist:wght@400;500;600"
        "&family=IBM+Plex+Mono:wght@400;500"
        "&display=swap"
    )

    css = """
:root{--bg:#0a0a0a;--text:#b0b0b0;--text-weak:#8a8a8a;--text-strong:#ededed;
--surface:#1a1a1a;--border:rgba(255,255,255,0.12);--accent-dim:rgba(255,255,255,0.08);
--font-sans:'Geist',system-ui,sans-serif;
--font-mono:'IBM Plex Mono',ui-monospace,Menlo,monospace}
*{margin:0;padding:0;box-sizing:border-box}
html{color-scheme:dark;font-size:14px;-webkit-font-smoothing:antialiased}
body{font-family:var(--font-sans);background:var(--bg);color:var(--text);
padding:40px 48px 80px;max-width:1100px;margin:0 auto;font-size:14px;line-height:1.6}
::selection{background:var(--text-strong);color:var(--bg)}
h1{font-family:var(--font-mono);font-size:1.75rem;font-weight:500;letter-spacing:-0.02em;
color:var(--text-strong);margin-bottom:8px;line-height:1.2}
.subtitle{font-family:var(--font-mono);color:var(--text-weak);font-size:0.75rem;
text-transform:uppercase;letter-spacing:0.05em}
.hero{display:flex;align-items:center;gap:32px;margin-bottom:24px}
.donut{width:96px;height:96px;flex-shrink:0}
.donut-arc{transition:stroke-dasharray .4s var(--ease)}
.donut-pct{fill:var(--text-strong);font-size:18px;font-weight:500;
font-family:var(--font-mono)}
.stats{display:inline-flex;gap:1px;flex-wrap:wrap;margin-bottom:8px;
border:1px solid var(--border);background:var(--border);width:fit-content;max-width:100%}
.stat{background:var(--bg);padding:14px 18px;min-width:110px;
transition:background 0.15s ease}
.stat.clickable{cursor:pointer}
.stat.clickable:hover{background:var(--surface)}
.stat.active{background:var(--surface)}
.stat-value{font-family:var(--font-mono);font-size:1.25rem;font-weight:500;
color:var(--text-strong);letter-spacing:-0.01em;font-variant-numeric:tabular-nums}
.stat-label{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
text-transform:uppercase;letter-spacing:0.1em;margin-top:6px;font-weight:500}
.stat-value.green{color:#22c55e}.stat-value.red{color:#ef4444}
.stat-value.yellow{color:#eab308}.stat-value.blue{color:#3b82f6}
.filter-hint{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
margin-bottom:24px;min-height:1em;text-transform:uppercase;letter-spacing:0.05em}
section{margin-bottom:48px}
section>h2{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
text-transform:uppercase;letter-spacing:0.1em;font-weight:500;margin-bottom:12px}
table{width:100%;border-collapse:collapse;margin-bottom:24px;font-size:0.875rem}
th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);
color:var(--text-strong);font-family:var(--font-mono);font-weight:500;font-size:0.75rem;
text-transform:none;letter-spacing:0}
td{padding:12px;border-bottom:1px solid var(--border);color:var(--text)}
td:first-child{font-family:var(--font-mono);color:var(--text-strong);font-size:0.8125rem}
td.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--text-weak);
font-family:var(--font-mono);font-size:0.8125rem}
td.detail-cell{color:var(--text-weak);font-size:0.8125rem;max-width:320px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr.expandable{cursor:pointer;transition:background 0.15s ease}
tr.expandable:hover{background:var(--accent-dim)}
tr.expandable.expanded{background:var(--accent-dim)}
tr.focused td{box-shadow:inset 2px 0 0 var(--text-strong)}
.badge{font-family:var(--font-mono);font-size:0.6875rem;padding:2px 10px;
border:1px solid var(--border);font-weight:500;letter-spacing:0.05em;
text-transform:uppercase;display:inline-block}
.badge.pass{color:#22c55e;border-color:rgba(34,197,94,0.4)}
.badge.fail{color:#ef4444;border-color:rgba(239,68,68,0.4)}
.badge.error{color:#eab308;border-color:rgba(234,179,8,0.4)}
.badge.uncertain{color:#3b82f6;border-color:rgba(59,130,246,0.4)}
.detail-row.hidden{display:none}
.detail-row td{padding:0;border-bottom:1px solid var(--border)}
.expand-panel{background:var(--surface);padding:20px 24px;border-left:2px solid var(--border)}
.panel-section{margin-bottom:20px}
.panel-section:last-child{margin-bottom:0}
.panel-label{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px;font-weight:500}
.panel-label.collapsible{cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px}
.panel-label.collapsible:hover{color:var(--text-strong)}
.caret{font-size:0.5rem;display:inline-block;transition:transform 0.15s ease}
.caret.open{transform:rotate(90deg)}
.checks-table{width:auto;margin:0;border-collapse:collapse}
.checks-table td{padding:4px 12px 4px 0;border:none;color:var(--text);
vertical-align:top;font-size:0.8125rem;font-family:var(--font-sans)}
.checks-table td:first-child{padding-right:8px;width:12px;font-family:inherit}
.check-name{color:var(--text-strong);font-family:var(--font-mono);font-size:0.75rem;
white-space:nowrap}
.dot-pass,.dot-fail{display:inline-block;width:6px;height:6px;position:relative;top:5px}
.dot-pass{background:#22c55e}
.dot-fail{background:#ef4444}
.judge-pass,.judge-fail{font-family:var(--font-sans);font-size:0.875rem;
line-height:1.6;color:var(--text)}
.judge-fail{color:#e5a8a8}
.evidence{margin:8px 0 0 18px;font-size:0.8125rem;color:var(--text-weak);line-height:1.6}
.evidence li{margin-bottom:4px}
.trace-grid{display:flex;gap:24px;flex-wrap:wrap}
.trace-grid>div{display:flex;flex-direction:column;gap:2px}
.trace-val{font-family:var(--font-mono);font-size:0.9375rem;color:var(--text-strong);
font-weight:500;font-variant-numeric:tabular-nums}
.trace-key{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
text-transform:uppercase;letter-spacing:0.05em}
.input-pre{font-family:var(--font-mono);font-size:0.8125rem;color:var(--text);
white-space:pre-wrap;word-break:break-word;margin:0;padding:14px 16px;
background:var(--bg);border:1px solid var(--border);max-height:240px;overflow:auto;
line-height:1.6}
.error-pre{font-family:var(--font-mono);font-size:0.8125rem;color:var(--text);
white-space:pre-wrap;word-break:break-word;margin:0;padding:14px 16px;
background:var(--bg);border:1px solid var(--border);max-height:400px;overflow-y:auto;
line-height:1.6}
.error-pre.collapsed{max-height:0;padding:0;border:none;overflow:hidden;
transition:max-height 0.2s ease}
.error-pre.expanded{max-height:400px;transition:max-height 0.3s ease}
kbd{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
background:var(--surface);border:1px solid var(--border);padding:1px 6px;
margin:0 2px}
.keyboard-hint{font-family:var(--font-mono);font-size:0.6875rem;color:var(--text-weak);
margin-top:32px;text-align:center}
@media (max-width:900px){body{padding:24px 20px 60px}
.hero{flex-direction:column;align-items:flex-start;gap:20px}
.stats{flex-direction:column}}
@media print{.detail-row.hidden{display:table-row!important}
.stat.clickable{cursor:default}.keyboard-hint{display:none}}
"""

    js = """
(function(){
  var rows=document.querySelectorAll('tr.expandable');
  var focusIdx=-1;
  // Click to expand/collapse
  rows.forEach(function(r){
    r.addEventListener('click',function(){toggleRow(r)});
  });
  function toggleRow(r){
    var next=r.nextElementSibling;
    if(!next||!next.classList.contains('detail-row'))return;
    var open=!next.classList.contains('hidden');
    next.classList.toggle('hidden',open);
    r.classList.toggle('expanded',!open);
  }
  // Collapsible error blocks
  document.querySelectorAll('.panel-label.collapsible').forEach(function(el){
    el.addEventListener('click',function(e){
      e.stopPropagation();
      var tgt=document.getElementById(el.dataset.target);
      if(!tgt)return;
      var open=tgt.classList.contains('expanded');
      tgt.classList.toggle('collapsed',open);
      tgt.classList.toggle('expanded',!open);
      el.querySelector('.caret').classList.toggle('open',!open);
    });
  });
  // Stat card filtering
  var cards=document.querySelectorAll('.stat.clickable');
  var hint=document.querySelector('.filter-hint');
  var activeFilter=null;
  cards.forEach(function(c){
    c.addEventListener('click',function(){
      var f=c.dataset.filter;
      if(activeFilter===f){activeFilter=null;c.classList.remove('active');applyFilter(null);return;}
      cards.forEach(function(x){x.classList.remove('active')});
      c.classList.add('active');
      activeFilter=f;
      applyFilter(f);
    });
  });
  function applyFilter(f){
    var allRows=document.querySelectorAll('tbody tr');
    allRows.forEach(function(r){
      if(!f){r.style.display='';return;}
      var s=r.dataset.status;
      if(!s){r.style.display='';return;}
      r.style.display=(s===f)?'':'none';
      if(r.classList.contains('detail-row')&&s===f){
        // Keep detail rows hidden unless expanded
        if(r.classList.contains('hidden'))r.style.display='none';
      }
    });
    if(hint)hint.textContent=f?'Showing '+f+' only — click again to clear':'';
    focusIdx=-1;
    document.querySelectorAll('tr.focused').forEach(function(r){r.classList.remove('focused')});
  }
  // Keyboard navigation
  function getVisibleRows(){
    return Array.from(rows).filter(function(r){return r.style.display!=='none'});
  }
  document.addEventListener('keydown',function(e){
    if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
    var visible=getVisibleRows();
    if(!visible.length)return;
    if(e.key==='j'||e.key==='ArrowDown'){
      e.preventDefault();
      focusIdx=Math.min(focusIdx+1,visible.length-1);
      setFocus(visible);
    }else if(e.key==='k'||e.key==='ArrowUp'){
      e.preventDefault();
      focusIdx=Math.max(focusIdx-1,0);
      setFocus(visible);
    }else if(e.key==='Enter'&&focusIdx>=0){
      e.preventDefault();
      toggleRow(visible[focusIdx]);
    }else if(e.key==='Escape'){
      // Collapse all
      document.querySelectorAll('tr.detail-row').forEach(function(r){r.classList.add('hidden')});
      document.querySelectorAll('tr.expanded').forEach(function(r){r.classList.remove('expanded')});
    }
  });
  function setFocus(visible){
    document.querySelectorAll('tr.focused').forEach(function(r){r.classList.remove('focused')});
    if(focusIdx>=0&&focusIdx<visible.length){
      visible[focusIdx].classList.add('focused');
      visible[focusIdx].scrollIntoView({block:'nearest'});
    }
  }
})();
"""

    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>kensa report</title>",
            '<link rel="preconnect" href="https://fonts.googleapis.com">',
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
            f'<link href="{font_url}" rel="stylesheet">',
            f"<style>{css}</style>",
            "</head>",
            "<body>",
            '<div class="hero">',
            donut,
            "<div>",
            "<h1>kensa</h1>",
            f'<div class="subtitle">{passed}/{total} passed</div>',
            "</div>",
            "</div>",
            f'<div class="stats">{stats_html}</div>',
            '<div class="filter-hint"></div>',
            "<section>",
            "<h2>Scenarios</h2>",
            "<table>",
            "<thead><tr>"
            "<th>Scenario</th><th>Status</th>"
            "<th>Checks</th><th>Judge</th>"
            '<th style="text-align:right">Cost</th>'
            '<th style="text-align:right">Duration</th>'
            "<th>Details</th>"
            "</tr></thead>",
            f"<tbody>{rows}</tbody>",
            "</table>",
            "</section>",
            '<div class="keyboard-hint">'
            "<kbd>j</kbd><kbd>k</kbd> navigate "
            "<kbd>Enter</kbd> expand "
            "<kbd>Esc</kbd> collapse all"
            "</div>",
            f"<script>{js}</script>",
            "</body>",
            "</html>",
        ]
    )


FORMATTERS = {
    "terminal": format_terminal,
    "markdown": format_markdown,
    "json": format_json,
    "html": format_html,
}
