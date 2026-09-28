from __future__ import annotations

import os
import sys
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

REPO_ROOT = Path(os.environ.get("SELF_IMPROVEMENT_REPO_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT))

from runner.dashboard_data import list_runs, load_run  # noqa: E402

TRAIN = "#137d79"
TEST = "#de684f"

st.set_page_config(page_title="Self-improvement monitor", page_icon="◎", layout="wide")
st.markdown(
    """
<style>
  .stApp { background: #f7f9f8; color: #1c302b; }
  .block-container { max-width: 1440px; padding-top: 1.3rem; }
  h1, h2, h3 { letter-spacing: 0 !important; }
  h1 { font-size: 1.55rem !important; font-weight: 700 !important; }
  h2 { font-size: 1.08rem !important; margin-top: .9rem !important; }
  [data-testid="stHeader"] { background: #f7f9f8; }
  .status-line { border-left: 3px solid #137d79; padding: .5rem .8rem;
                 background: #eaf2ef; margin: .6rem 0 1rem; color: #28453e; }
  .score-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
                 border-top: 1px solid #cfdad5; border-bottom: 1px solid #cfdad5;
                 margin: 0 0 .6rem; padding: .75rem 0; }
  .score-cell { border-right: 1px solid #dce5e0; padding: .15rem 1rem; }
  .score-cell:first-child { padding-left: 0; }
  .score-cell:last-child { border-right: none; }
  .score-label { color: #5e706a; font-size: .78rem; font-weight: 600; }
  .score-value { color: #1c302b; font-size: 1.55rem; font-weight: 700; line-height: 1.25; }
  .score-cell.test .score-value { color: #c45641; }
  .timeline-entry { border-left: 2px solid #b9d7d0; padding: .3rem .9rem; margin-bottom: .9rem; }
  @media (max-width: 700px) {
    .score-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: .75rem; }
    .score-cell:nth-child(2) { border-right: none; }
  }
</style>
""",
    unsafe_allow_html=True,
)


def _score_chart(rows: list[dict[str, Any]], metric: str, title: str) -> go.Figure:
    fig = go.Figure()
    for split, color in (("train", TRAIN), ("test", TEST)):
        data = [row for row in rows if row.get(f"{split}_{metric}") is not None]
        fig.add_trace(
            go.Scatter(
                x=[row["epoch"] for row in data],
                y=[row[f"{split}_{metric}"] * 100 for row in data],
                name=split.title(),
                mode="lines+markers",
                line={"color": color, "width": 2.5},
                marker={"size": 8},
                customdata=[
                    [
                        row.get(f"{split}_number_of_evaluated_tasks", 0),
                        row.get(f"{split}_number_of_unevaluated_tasks", 0),
                        row.get(f"{split}_number_of_failed_trials", 0),
                    ]
                    for row in data
                ],
                hovertemplate=(
                    "%{fullData.name} · epoch %{x}<br>Pass rate %{y:.1f}%"
                    "<br>Tasks scored %{customdata[0]} · skipped %{customdata[1]}"
                    "<br>Failed trials %{customdata[2]}<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        title={"text": title, "font": {"size": 15, "color": "#253933"}},
        height=265,
        margin={"l": 35, "r": 12, "t": 45, "b": 35},
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font={"color": "#354e46", "size": 12},
        legend={"orientation": "h", "y": 1.14, "x": 0.68, "font": {"color": "#354e46"}},
        xaxis={
            "title": {"text": "Epoch", "font": {"color": "#4d645c"}},
            "tickfont": {"color": "#52685f"},
            "dtick": 1,
            "gridcolor": "#eef1ef",
        },
        yaxis={
            "title": {"text": "Pass rate", "font": {"color": "#4d645c"}},
            "tickfont": {"color": "#52685f"},
            "range": [0, 105],
            "ticksuffix": "%",
            "gridcolor": "#e7ece9",
        },
        hovermode="closest",
    )
    return fig


def _render(data: dict[str, Any]) -> None:
    progress = data["progress"]
    rows = data["metrics"]
    splits = data["splits"]
    current_epoch = progress.get("epoch", max((row["epoch"] for row in rows), default=0))
    phase = progress.get("phase", "historical run")
    status = progress.get("status", "recorded")

    st.title("Self-improvement monitor")
    st.caption(f"{data['run_id']}   ·   {data['metadata'].get('config_id', 'local run')}")
    message = f" · {escape(str(progress['message']))}" if progress.get("message") else ""
    active = next((s for s in splits if s["epoch"] == current_epoch and s["kind"] == phase), None)
    trial_states = [t.get("status") for t in active["trials"]] if active else []
    executing = sum(state in {"executing", "awaiting_evaluation"} for state in trial_states)
    evaluating = trial_states.count("evaluating")
    scored = trial_states.count("scored")
    failed = trial_states.count("failed")
    live_counts = (
        f" · {executing} executing · {evaluating} evaluating · {scored} scored · {failed} failed"
        if active and status == "running"
        else ""
    )
    st.markdown(
        f'<div class="status-line"><strong>{status.title()}</strong> · Epoch {current_epoch} · '
        f"{phase.title()}{live_counts}{message}"
        "</div>",
        unsafe_allow_html=True,
    )
    latest = rows[-1] if rows else {}
    if rows:
        cells = []
        for split, metric, label in (
            ("train", "task_pass_rate", "Train tasks"),
            ("test", "task_pass_rate", "Test tasks"),
            ("train", "rubric_pass_rate", "Train rubrics"),
            ("test", "rubric_pass_rate", "Test rubrics"),
        ):
            value = latest.get(f"{split}_{metric}")
            displayed = f"{value:.1%}" if value is not None else "—"
            cells.append(
                f'<div class="score-cell {split}"><div class="score-label">{label}</div>'
                f'<div class="score-value">{displayed}</div></div>'
            )
        st.markdown('<div class="score-strip">' + "".join(cells) + "</div>", unsafe_allow_html=True)

    st.subheader("Performance")
    if rows:
        left, right = st.columns(2)
        with left:
            st.plotly_chart(_score_chart(rows, "task_pass_rate", "Task pass rate"), width="stretch")
        with right:
            st.plotly_chart(
                _score_chart(rows, "rubric_pass_rate", "Rubric pass rate"), width="stretch"
            )
    else:
        st.info("Scores appear as each split finishes evaluation.")

    st.subheader("Epoch activity")
    if splits:
        activity = []
        for split in sorted(splits, key=lambda item: (item["epoch"], item["kind"]), reverse=True):
            states = [item.get("status") for item in split["trials"]]
            activity.append(
                {
                    "Epoch": split["epoch"],
                    "Split": split["kind"].title(),
                    "Status": split["status"].title(),
                    "Queued": states.count("queued"),
                    "Executing": states.count("executing") + states.count("awaiting_evaluation"),
                    "Evaluating": states.count("evaluating"),
                    "Scored": states.count("scored"),
                    "Failed": states.count("failed"),
                }
            )
        st.dataframe(activity, hide_index=True, width="stretch")
        if active and active["trials"]:
            with st.expander("Current trial details"):
                st.dataframe(active["trials"], hide_index=True, width="stretch")

    st.subheader("Cost and usage")
    train_cost = sum(s["generation_cost_usd"] for s in splits if s["kind"] == "train")
    test_cost = sum(s["generation_cost_usd"] for s in splits if s["kind"] == "test")
    judge_values = [s["judge_cost_usd"] for s in splits if s["judge_cost_usd"] is not None]
    codex_input = sum(i["usage"].get("input_tokens", 0) for i in data["timeline"])
    codex_output = sum(i["usage"].get("output_tokens", 0) for i in data["timeline"])
    cost_cols = st.columns(5)
    cost_cols[0].metric("Train generation", f"${train_cost:.2f}")
    cost_cols[1].metric("Test generation", f"${test_cost:.2f}")
    cost_cols[2].metric("Judge estimate", f"${sum(judge_values):.2f}" if judge_values else "—")
    cost_cols[3].metric("Codex input tokens", f"{codex_input:,}")
    cost_cols[4].metric("Codex output tokens", f"{codex_output:,}")
    st.caption(
        "Generation is Introspection-reported cost. Judge is estimated from GPT-6 Sol usage "
        "for new runs; older runs may lack it. Codex tokens are usage, not a dollar charge "
        "for ChatGPT sign-in."
    )
    if splits:
        cost_rows = []
        for epoch in sorted({item["epoch"] for item in splits}):
            by_kind = {item["kind"]: item for item in splits if item["epoch"] == epoch}
            judge_costs = [
                item["judge_cost_usd"]
                for item in by_kind.values()
                if item["judge_cost_usd"] is not None
            ]
            cost_rows.append(
                {
                    "Epoch": epoch,
                    "Train generation ($)": round(
                        by_kind.get("train", {}).get("generation_cost_usd", 0), 3
                    ),
                    "Test generation ($)": round(
                        by_kind.get("test", {}).get("generation_cost_usd", 0), 3
                    ),
                    "Judge estimate ($)": round(sum(judge_costs), 3) if judge_costs else None,
                }
            )
        st.dataframe(cost_rows, hide_index=True, width="stretch")

    st.subheader("Research log")
    if data["timeline"]:
        for entry in reversed(data["timeline"]):
            result = entry["result"]
            summary = result.get("summary", "No summary")
            state = result.get("verdict", result.get("status", "completed"))
            st.markdown(
                f'<div class="timeline-entry"><strong>{entry["epoch"]} · '
                f"{entry['role'].title()} · {escape(str(state))}</strong><br>"
                f"{escape(str(summary))}</div>",
                unsafe_allow_html=True,
            )
            if result.get("hypothesis"):
                st.caption(f"Hypothesis: {result['hypothesis']}")
            if result.get("issues"):
                st.caption(f"Issues: {result['issues']}")
    else:
        st.caption("Researcher and verifier decisions will appear after the first revision.")


runs = list_runs(REPO_ROOT)
selected: str | None
with st.sidebar:
    st.header("Runs")
    if runs:
        requested = st.query_params.get("run") or os.environ.get("SELF_IMPROVEMENT_RUN_ID")
        selected = st.selectbox(
            "Select run", runs, index=runs.index(requested) if requested in runs else 0
        )
        st.query_params["run"] = selected
        st.caption(f"{len(runs)} local run{'s' if len(runs) != 1 else ''}")
    else:
        selected = None


@st.fragment(run_every="3s")
def live_view(run_id: str) -> None:
    _render(load_run(REPO_ROOT, run_id))


if selected:
    live_view(selected)
else:
    st.title("Self-improvement monitor")
    st.info("No self-improvement runs found yet. Start a run to see live progress here.")
