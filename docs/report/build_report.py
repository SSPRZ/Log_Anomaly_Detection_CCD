"""Build the IEEE-format project report (two-column, Times) as a PDF.

    .venv/Scripts/python docs/report/build_report.py

Writes docs/report/IEEE_Report_Log_Anomaly_Detection.pdf. All figures in the text come from the project's own
evaluation reports (evaluation/reports/*.md) and the live-demo screenshots in docs/demo-screenshots.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, Group, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, FrameBreak, Image, KeepTogether, NextPageTemplate,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "IEEE_Report_Log_Anomaly_Detection.pdf"
SHOTS = ROOT / "docs" / "demo-screenshots"

# ----------------------------------------------------------------------------- fonts (Times, as IEEE requires)
FONT_DIR = Path("C:/Windows/Fonts")
pdfmetrics.registerFont(TTFont("Times", str(FONT_DIR / "times.ttf")))
pdfmetrics.registerFont(TTFont("Times-B", str(FONT_DIR / "timesbd.ttf")))
pdfmetrics.registerFont(TTFont("Times-I", str(FONT_DIR / "timesi.ttf")))
pdfmetrics.registerFont(TTFont("Times-BI", str(FONT_DIR / "timesbi.ttf")))
pdfmetrics.registerFontFamily("Times", normal="Times", bold="Times-B", italic="Times-I", boldItalic="Times-BI")

# ----------------------------------------------------------------------------- page geometry (IEEE conference, US Letter)
PAGE_W, PAGE_H = letter
M_LEFT = M_RIGHT = 0.625 * inch
M_TOP = 0.75 * inch
M_BOTTOM = 1.0 * inch
GAP = 0.25 * inch
COL_W = (PAGE_W - M_LEFT - M_RIGHT - GAP) / 2
TITLE_H = 1.8 * inch

# ----------------------------------------------------------------------------- styles
body = ParagraphStyle("body", fontName="Times", fontSize=10, leading=12, alignment=TA_JUSTIFY, firstLineIndent=10)
body_ni = ParagraphStyle("body_ni", parent=body, firstLineIndent=0)
title = ParagraphStyle("title", fontName="Times", fontSize=24, leading=28, alignment=TA_CENTER, spaceAfter=14)
author = ParagraphStyle("author", fontName="Times", fontSize=11, leading=13, alignment=TA_CENTER)
author_i = ParagraphStyle("author_i", parent=author, fontName="Times-I", fontSize=10, leading=12)
abstract = ParagraphStyle("abstract", fontName="Times-B", fontSize=9, leading=10.4, alignment=TA_JUSTIFY)
h1 = ParagraphStyle("h1", fontName="Times", fontSize=10, leading=12, alignment=TA_CENTER, spaceBefore=8, spaceAfter=4)
h2 = ParagraphStyle("h2", fontName="Times-I", fontSize=10, leading=12, alignment=TA_LEFT, spaceBefore=5, spaceAfter=2)
caption = ParagraphStyle("caption", fontName="Times", fontSize=8, leading=9.4, alignment=TA_JUSTIFY, spaceBefore=3,
                         spaceAfter=8)
tcap = ParagraphStyle("tcap", fontName="Times", fontSize=8, leading=9.6, alignment=TA_CENTER, spaceAfter=3)
cell = ParagraphStyle("cell", fontName="Times", fontSize=8, leading=9.2, alignment=TA_LEFT)
cell_c = ParagraphStyle("cell_c", parent=cell, alignment=TA_CENTER)
cell_b = ParagraphStyle("cell_b", parent=cell, fontName="Times-B")
ref = ParagraphStyle("ref", fontName="Times", fontSize=8, leading=9.4, alignment=TA_JUSTIFY, leftIndent=16,
                     firstLineIndent=-16, spaceAfter=2)
eq = ParagraphStyle("eq", fontName="Times", fontSize=10, leading=12, alignment=TA_CENTER)
eqn = ParagraphStyle("eqn", fontName="Times", fontSize=10, leading=12, alignment=TA_RIGHT)
bullet = ParagraphStyle("bullet", parent=body_ni, leftIndent=12, bulletIndent=2, spaceAfter=1)

_sec = [0]
_sub = [0]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def P(text, style=body):
    return Paragraph(text, style, bulletText="•" if style is bullet else None)


def section(name):
    _sec[0] += 1
    _sub[0] = 0
    # IEEE section heads: Roman numeral, small caps (rendered as capitals), centered
    return [CondPageBreak(0.9 * inch), P(f"{ROMAN[_sec[0] - 1]}. {name.upper()}", h1)]


def subsection(name):
    _sub[0] += 1
    return P(f"{chr(64 + _sub[0])}. {name}", h2)


_eq = [0]


def equation(expr):
    _eq[0] += 1
    t = Table([[P(expr, eq), P(f"({_eq[0]})", eqn)]], colWidths=[COL_W - 24, 24])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


_tab = [0]


def table(caption_text, rows, widths, header_rows=1, center_cols=()):
    _tab[0] += 1
    data = []
    for i, r in enumerate(rows):
        st = cell_b if i < header_rows else cell
        data.append([P(str(c), cell_c if (j in center_cols and i >= header_rows) else st) for j, c in enumerate(r)])
    t = Table(data, colWidths=[w * COL_W for w in widths], repeatRows=header_rows)
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.black),
        ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.5, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
    ]))
    head = P(f"TABLE {ROMAN[_tab[0] - 1]}<br/>{caption_text.upper()}", tcap)
    return KeepTogether([Spacer(1, 4), head, t, Spacer(1, 8)])


_fig = [0]


def figure(flowable, caption_text):
    _fig[0] += 1
    return KeepTogether([Spacer(1, 4), flowable, P(f"Fig. {_fig[0]}. {caption_text}", caption)])


# ----------------------------------------------------------------------------- figure 1: architecture
def architecture_drawing():
    w, h = COL_W, 268
    d = Drawing(w, h)
    stages = [
        ("Services + Fluent Bit", "structured JSON logs, shipped from stdout"),
        ("Apache Kafka (KRaft)", "logs.raw, schema-checked, replayable"),
        ("Stream processing", "parse, PII redaction, Drain templates, 1-min windows"),
        ("Anomaly scorer", "T1 statistics, T2 isolation forest, T3 LSTM, rules"),
        ("Alert router", "de-duplicate, group, P1-P3, feedback"),
    ]
    bw, bh, x0 = 150, 38, 4
    gap = (h - len(stages) * bh) / (len(stages) - 1)
    ys = []
    for i, (name, sub) in enumerate(stages):
        y = h - (i + 1) * bh - i * gap
        ys.append(y)
        d.add(Rect(x0, y, bw, bh, rx=4, ry=4, strokeColor=colors.HexColor("#1F3B63"), strokeWidth=0.9,
                   fillColor=colors.HexColor("#EAF0F8")))
        d.add(String(x0 + bw / 2, y + bh - 14, name, fontName="Times-B", fontSize=8.6, textAnchor="middle"))
        # wrap the subtitle into two short lines
        words, lines, cur = sub.split(), [], ""
        for wd in words:
            if len(cur) + len(wd) + 1 > 34:
                lines.append(cur)
                cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        for k, ln in enumerate(lines[:2]):
            d.add(String(x0 + bw / 2, y + bh - 24 - 8 * k, ln, fontName="Times", fontSize=6.8, textAnchor="middle"))
        if i < len(stages) - 1:
            cx = x0 + bw / 2
            d.add(Line(cx, y, cx, y - gap + 5, strokeWidth=0.9))
            d.add(Polygon([cx - 3.2, y - gap + 6, cx + 3.2, y - gap + 6, cx, y - gap], fillColor=colors.black,
                          strokeColor=colors.black, strokeWidth=0.1))
    # storage / lifecycle column
    sx, sw = x0 + bw + 22, w - (x0 + bw + 22) - 2
    side = [("ClickHouse", "logs, features, alerts"), ("S3 Parquet archive", "training + replay"),
            ("MLflow registry", "champion / challenger"), ("Prometheus", "pipeline SLOs, canary")]
    sh = 40
    sgap = (h - len(side) * sh) / (len(side) - 1)
    for i, (name, sub) in enumerate(side):
        y = h - (i + 1) * sh - i * sgap
        d.add(Rect(sx, y, sw, sh, rx=4, ry=4, strokeColor=colors.HexColor("#8A6D3B"), strokeWidth=0.8,
                   strokeDashArray=[2, 1.5], fillColor=colors.HexColor("#FBF5E8")))
        d.add(String(sx + sw / 2, y + sh - 15, name, fontName="Times-B", fontSize=8, textAnchor="middle"))
        d.add(String(sx + sw / 2, y + sh - 27, sub, fontName="Times", fontSize=6.8, textAnchor="middle"))
    # dashed links from the pipeline to the stores
    for yy in (ys[1] + bh / 2, ys[2] + bh / 2, ys[3] + bh / 2):
        d.add(Line(x0 + bw, yy, sx, yy, strokeWidth=0.5, strokeDashArray=[2, 2], strokeColor=colors.grey))
    return d


# ----------------------------------------------------------------------------- figure 2: per-fault recall
def recall_chart():
    d = Drawing(COL_W, 178)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 34, 52, COL_W - 40, 116
    bc.data = [
        [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],      # platform
        [1.0, 0.5, 0.5, 0.0, 0.0, 0.0],      # static >10 errors / 5 min
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],      # static >50 errors / 5 min
    ]
    bc.categoryAxis.categoryNames = ["Timeout", "Crash loop", "Bad deploy", "Silent", "Mem. leak", "Skip step"]
    bc.categoryAxis.labels.fontName = "Times"
    bc.categoryAxis.labels.fontSize = 7
    bc.categoryAxis.labels.angle = 0
    bc.categoryAxis.labels.dy = -2
    bc.valueAxis.valueMin, bc.valueAxis.valueMax, bc.valueAxis.valueStep = 0, 1.0, 0.25
    bc.valueAxis.labels.fontName = "Times"
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.labelTextFormat = "%.2f"
    bc.groupSpacing, bc.barSpacing = 6, 0.8
    fills = [colors.HexColor("#1F3B63"), colors.HexColor("#D98C3A"), colors.HexColor("#9A9A9A")]
    for i, f in enumerate(fills):
        bc.bars[i].fillColor = f
        bc.bars[i].strokeColor = f
    d.add(bc)
    label = Group(String(0, 0, "Recall", fontName="Times", fontSize=7.5, textAnchor="middle"))
    label.transform = (0, 1, -1, 0, 8, 52 + 58)  # rotated 90 degrees, centred on the value axis
    d.add(label)
    lg = Legend()
    lg.x, lg.y = 34, 26
    lg.fontName, lg.fontSize = "Times", 7.5
    lg.alignment = "right"
    lg.columnMaximum = 2
    lg.deltax = 112
    lg.deltay = 10
    lg.dx, lg.dy = 7, 7
    lg.colorNamePairs = [(fills[0], "Proposed platform"), (fills[1], "Static >10 err/5 min"),
                         (fills[2], "Static >50 err/5 min")]
    d.add(lg)
    return d


def screenshot(name, width=COL_W):
    img = Image(str(SHOTS / name))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width, width * ratio
    return img


# ----------------------------------------------------------------------------- page templates
def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times", 8)
    canvas.drawCentredString(PAGE_W / 2, 0.5 * inch, str(doc.page))
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUT), pagesize=letter, leftMargin=M_LEFT, rightMargin=M_RIGHT, topMargin=M_TOP,
                          bottomMargin=M_BOTTOM, title="A Streaming Platform for Real-Time Log Anomaly Detection",
                          author="Pranshu Thakkar, Ritwik Gupta, Zaid Khan, Saatvik Das, Vedula Srisaket Rama",
                          subject="IEEE-format project report: distributed log anomaly detection")
    body_h = PAGE_H - M_TOP - M_BOTTOM
    first = PageTemplate(id="first", onPage=footer, frames=[
        Frame(M_LEFT, PAGE_H - M_TOP - TITLE_H, PAGE_W - M_LEFT - M_RIGHT, TITLE_H, id="title", leftPadding=0,
              rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(M_LEFT, M_BOTTOM, COL_W, body_h - TITLE_H, id="c1", leftPadding=0, rightPadding=0, topPadding=0,
              bottomPadding=0),
        Frame(M_LEFT + COL_W + GAP, M_BOTTOM, COL_W, body_h - TITLE_H, id="c2", leftPadding=0, rightPadding=0,
              topPadding=0, bottomPadding=0),
    ])
    later = PageTemplate(id="later", onPage=footer, frames=[
        Frame(M_LEFT, M_BOTTOM, COL_W, body_h, id="l1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(M_LEFT + COL_W + GAP, M_BOTTOM, COL_W, body_h, id="l2", leftPadding=0, rightPadding=0, topPadding=0,
              bottomPadding=0),
    ])
    doc.addPageTemplates([first, later])
    s = []

    # ------------------------------------------------------------------------- title block
    s += [
        P("A Streaming Platform for Real-Time Log Anomaly Detection with Explainable Alerts", title),
        P("Pranshu Thakkar, Ritwik Gupta, Zaid Khan, Saatvik Das, Vedula Srisaket Rama", author),
        P("DevOps / Platform Engineering", author_i),
        P("[Institution / Organization]", author_i),
        NextPageTemplate("later"),
        FrameBreak(),
    ]

    # ------------------------------------------------------------------------- abstract
    s += [
        P("<i>Abstract</i>—Production services emit millions of log lines a day, yet most alerting still relies on "
          "static, hand-tuned thresholds that are noisy at peak load, blind at quiet hours and unable to recognise "
          "failure modes nobody anticipated. This paper presents the design, implementation and evaluation of a "
          "streaming log anomaly detection platform that ingests structured logs through Apache Kafka, redacts "
          "personal data, mines log templates with Drain, aggregates one-minute behavioural features per service "
          "and scores every window with a three-tier ensemble: robust statistical baselines with a drift detector, "
          "a per-group isolation forest, and a DeepLog-style LSTM over request traces, complemented by hard rules. "
          "An alert router de-duplicates, groups related services into incidents and attaches an explanation to "
          "every alert. On a simulated five-service e-commerce workload with labelled fault injection covering six "
          "failure types, the platform detected all 12 injected incidents with an incident-level precision of "
          "0.91 (F1 0.95), raised 2.3 alerts per service per day and kept the 95th-percentile log-to-score "
          "latency at 37.6 s, whereas the static rule in use today detected none of the incidents. A second, "
          "independent run with 18 incidents reproduced these results (precision 0.89, recall 1.00). We also "
          "report fifteen defects that surfaced only when the full containerised stack was run, and the lessons "
          "they hold for deploying such pipelines.", abstract),
        Spacer(1, 6),
        P("<i>Index Terms</i>—log analysis, anomaly detection, stream processing, Apache Kafka, isolation forest, "
          "LSTM, AIOps, site reliability engineering.", abstract),
        Spacer(1, 4),
    ]

    # ------------------------------------------------------------------------- I. introduction
    s += section("Introduction")
    s += [
        P("Logs are the most detailed record of what a software service is doing, but in most organisations they "
          "are consulted only after users report a failure. Alerting on logs typically means static rules such as "
          "“more than 50 errors in 5 minutes”. Such rules suffer from three well-known problems [10], [11]: they do "
          "not adapt to diurnal and weekly traffic patterns, so they fire constantly at peak and stay silent at "
          "night; they only recognise the failures someone wrote a rule for; and they produce duplicate, "
          "context-free alerts that slow down responders and erode trust in alerting."),
        P("This work builds and evaluates a platform that replaces such thresholds with learned behaviour. Its "
          "goals, set out in the project proposal, are to centralise logs in a durable, replayable stream, to "
          "detect anomalous behaviour within 60 s of the log event (95th percentile), to catch failure modes "
          "nobody wrote a rule for with at least 0.80 precision and 0.70 recall, to keep alerts below five per "
          "service per day with an explanation attached to each, and to learn from responder feedback."),
        P("The contributions of this paper are:", body),
        P("an end-to-end streaming architecture (Kafka, stream jobs with event-time windows and per-source "
          "watermarks, ClickHouse, object storage) that can be replayed and runs both as a single process and as "
          "a 20-service container stack;", bullet, ),
        P("a label-free, three-tier detection ensemble with explanations, a feedback loop and a gated "
          "champion/challenger model lifecycle;", bullet),
        P("an incident-level evaluation with labelled fault injection that compares the platform with the static "
          "rules in use today;", bullet),
        P("an account of the defects found when the full stack was deployed, which are instructive for anyone "
          "operating similar pipelines.", bullet),
    ]

    # ------------------------------------------------------------------------- II. related work
    s += section("Related Work")
    s += [
        P("<i>Log parsing.</i> Automated log analysis usually begins by separating the constant part of a message "
          "(the template) from its variables. Drain [1] does this online with a fixed-depth parse tree and is among "
          "the most accurate and efficient parsers in the LogPAI benchmark of Zhu <i>et al.</i> [6]; we use it for "
          "that reason."),
        P("<i>Log-based anomaly detection.</i> Early work mined console logs with PCA over message-count vectors "
          "[5]. He <i>et al.</i> [4] compared supervised and unsupervised detectors and found that unsupervised "
          "methods trade some accuracy for not needing labels, which most organisations lack. DeepLog [2] models "
          "the sequence of log keys with an LSTM and flags keys outside the top-<i>k</i> predictions; our third "
          "tier follows this design. A broad survey of the field is given in [10]."),
        P("<i>Outlier detection and robust statistics.</i> The isolation forest [3] isolates anomalies with random "
          "axis-parallel splits and needs no labels; robust location and scale estimators such as the median "
          "absolute deviation [9] keep baselines from being distorted by the very outliers they are meant to find."),
        P("<i>Streaming infrastructure.</i> Kafka [7] provides the durable, partitioned log that decouples our "
          "stages, and the event-time windowing with watermarks we use follows the Dataflow model [12] as "
          "implemented in Apache Flink [8]. Model versions are tracked with MLflow [13]."),
        P("Compared with prior work, which mostly evaluates detectors offline on fixed datasets, this project "
          "integrates parsing, multi-tier detection, explanation, alert routing and the model lifecycle in one "
          "operational system and measures it end to end, at the incident level, against the alerting it would "
          "replace."),
    ]

    # ------------------------------------------------------------------------- III. architecture
    s += section("System Architecture")
    s += [
        P("The platform is a five-stage streaming pipeline (Fig. 1). Every hop between stages is a Kafka topic, "
          "which decouples the components, absorbs bursts and allows history to be replayed whenever a parser or "
          "model changes. Each stage is a consumer group that commits offsets only after its output has been "
          "flushed, giving at-least-once delivery; the router de-duplicates downstream."),
        figure(architecture_drawing(),
               "System architecture. Solid arrows show the real-time path through Kafka topics; dashed boxes are "
               "the storage, training and observability services."),
        subsection("Ingestion"),
        P("Services log one JSON object per line to standard output through a shared library (Python and Go) that "
          "enforces a common envelope (<i>ts, service, env, level, msg</i>, plus optional <i>trace_id</i> and "
          "attributes such as latency and HTTP status). Fluent Bit ships the lines to the <i>logs.raw</i> topic "
          "keyed by service, so a service’s records stay ordered within a partition. Kafka runs in KRaft mode with "
          "producers using <i>acks=all</i> and idempotence; topics carry 72 h of history."),
        subsection("Stream Processing"),
        P("The <i>parse-enrich</i> job validates each record against the envelope (invalid or oversized records go "
          "to a dead-letter topic with a reason), parses legacy plain-text formats with regex profiles, normalises "
          "timestamps, redacts personal data (e-mail addresses, card-like numbers, IP addresses and tokens) before "
          "anything is stored, and assigns a Drain template identifier. Template identifiers are derived from the "
          "service and the template text, so they are stable across restarts."),
        P("The <i>window-features</i> job aggregates one-minute tumbling event-time windows per service. The "
          "watermark is the minimum, over active sources, of each source’s newest event time minus a 5 s bound on "
          "disorder; a source more than 10 s behind the newest is treated as idle so that a silent service cannot "
          "stall the watermark. Records up to 30 s late update their window as a revision. Crucially, a service "
          "that sends nothing still receives a feature vector with a growing <i>seconds-since-last</i> value, "
          "which is how silence becomes observable. A session window keyed by <i>trace_id</i> (30 s gap) emits "
          "each request’s ordered template sequence for the sequence model."),
        subsection("Storage and Lifecycle"),
        P("ClickHouse holds redacted logs (14-day TTL), templates, feature vectors (90 days), anomaly scores and "
          "alerts; sinks buffer rows and insert in large batches. Every topic is also archived hourly as Parquet "
          "in S3-compatible object storage for training and replay. A nightly job trains new models and registers "
          "them in MLflow."),
        subsection("Alert Routing"),
        P("The router turns scores into a small number of notifications. Anomalies on the same service within "
          "15 min join one open alert; alerts on services that call each other within 2 min share an incident "
          "identifier. Severity follows a routing policy kept in version control: P1 (score ≥ 0.9, or a hard rule "
          "on a tier-1 service) pages on-call, P2 goes to the team channel and P3 is dashboard-only. Delivery runs "
          "through Prometheus Alertmanager, and each alert carries <i>Real issue / False alarm</i> buttons whose "
          "verdicts tune per-service thresholds weekly and label the next training run."),
    ]

    # ------------------------------------------------------------------------- IV. detection
    s += section("Anomaly Detection Method")
    s += [
        P("Because labelled incidents are rare, every tier is unsupervised. Each tier yields a score in [0, 1] for "
          "each service-minute window."),
        table("Feature vector per service and minute", [
            ["Group", "Features"],
            ["Volume", "total count; change vs. same minute last week; distinct hosts"],
            ["Severity", "error and warning counts; error ratio; fatal flag"],
            ["Content", "unique templates; templates new in 7 days; template-distribution entropy; 64-bucket "
                        "hashed template histogram"],
            ["Performance", "p50 / p95 / p99 latency; ratio of 5xx responses"],
            ["Liveness", "seconds since the service’s last record"],
        ], [0.25, 0.75]),
        subsection("Tier 1: Robust Statistical Baseline"),
        P("For each service <i>s</i>, hour of week <i>h</i> and feature <i>f</i>, a rolling sample of recent "
          "<i>normal</i> values yields the median <i>m</i> and median absolute deviation (MAD). A window is scored "
          "with the robust z-score"),
        equation("<i>z</i><sub><i>f</i></sub> = (<i>x</i><sub><i>f</i></sub> − <i>m</i><sub><i>s,h,f</i></sub>) / "
                 "(1.4826 · max(MAD<sub><i>s,h,f</i></sub>, <i>δ</i><sub><i>f</i></sub>))"),
        P("where the per-feature floor <i>δ</i> keeps near-constant features from producing huge "
          "scores. Slots with fewer than 20 samples fall back to hour-of-day and then service-wide statistics, and "
          "only the direction that indicates trouble counts (e.g., rising latency, falling host count). Slow drifts "
          "such as memory leaks are caught by an exponentially weighted moving average of the signed z-score,"),
        equation("<i>e</i><sub><i>t</i></sub> = (1 − <i>α</i>) <i>e</i><sub><i>t</i>−1</sub> + <i>α</i> "
                 "<i>z</i><sub><i>t</i></sub>, &nbsp;&nbsp; <i>z</i><sub>trend</sub> = (<i>e</i><sub><i>t</i></sub> "
                 "− <i>μ</i><sub><i>e</i></sub>) / <i>σ</i><sub><i>e</i></sub>"),
        P("with <i>α</i> = 0.1, where the mean and standard deviation of the EWMA are calibrated out of "
          "sample on the last two days of history. The EWMA is not updated on windows that are already acutely "
          "anomalous (|<i>z</i>| ≥ 6), so a short outage does not leave a long tail. The tier score maps the "
          "strongest deviations through a logistic function centred at <i>z</i> = 4.5."),
        subsection("Tier 2: Isolation Forest"),
        P("One isolation forest [3] (100 trees) per service group scores the full vector of scalar features, each "
          "robust-scaled per service so a single model can serve services of very different volume. The 64 hashed "
          "template buckets are summarised by one feature, the Jensen–Shannon divergence [14] between the "
          "window’s template distribution <i>p</i> and the service’s normal distribution <i>q</i>:"),
        equation("JS(<i>p</i> ‖ <i>q</i>) = ½ KL(<i>p</i> ‖ <i>m</i>) + ½ KL(<i>q</i> ‖ <i>m</i>), &nbsp; "
                 "<i>m</i> = ½(<i>p</i> + <i>q</i>)"),
        P("Raw forest scores are calibrated against the training distribution and exported to ONNX for serving, "
          "with a parity check against the original model. Since a forest cannot rank a point beyond its training "
          "range above the range’s edge, single-feature extremes are left to Tier 1 and Tier 2 contributes unusual "
          "<i>combinations</i> of individually plausible values."),
        subsection("Tier 3: Sequence Model"),
        P("Following DeepLog [2], an LSTM (embedding 32, two layers of 64 units) predicts the next "
          "<i>service:template</i> token of a trace from the previous four; a token outside the top-3 predictions "
          "is a violation. Only the first violation of a trace counts, and it is attributed to the service whose "
          "step was <i>expected</i>: after one deviation every later step looks wrong, and a missing step (for "
          "example, a skipped payment) is the absent service’s problem. The tier score is a binomial test of the "
          "share of violating traces against the service’s normal violation rate."),
        subsection("Hard Rules, Ensemble and Debounce"),
        P("Two high-precision rules complement the models: a template first seen within ten minutes at ERROR or "
          "FATAL level occurring at least three times, and a service that stops logging for more than 90 s. "
          "Tier scores are combined as a weighted mean over the tiers present,"),
        equation("<i>S</i> = max( Σ<sub><i>i</i></sub> <i>w</i><sub><i>i</i></sub><i>s</i><sub><i>i</i></sub> / "
                 "Σ<sub><i>i</i></sub> <i>w</i><sub><i>i</i></sub>, &nbsp;0.85 · max<sub><i>i</i></sub> "
                 "<i>s</i><sub><i>i</i></sub> · 1[max<sub><i>i</i></sub> <i>s</i><sub><i>i</i></sub> ≥ 0.9] )"),
        P("so that one very confident tier is not averaged away. An alert fires when <i>S</i> exceeds the threshold "
          "in two of the last three windows; hard rules bypass this debounce. Every anomaly records its top three "
          "deviating features, the templates whose counts moved most and five redacted sample lines."),
        subsection("Model Lifecycle"),
        P("A nightly job trains on the last 14 days (7 in our experiments), holds out the most recent 20 % and adds "
          "synthetic anomalies (volume drops and spikes, error bursts, latency inflation, new templates, template-mix "
          "shifts, host loss). It tunes the ensemble weights and threshold for F1 under an alert budget of five per "
          "service per day, and promotes a model only if F1 does not regress against the champion on the same "
          "evaluation set and the projected alert rate stays within budget. New models shadow the champion before "
          "promotion, scorers hot-reload the champion, and a one-command rollback restores the previous version. "
          "Drift is monitored with the population stability index."),
    ]

    # ------------------------------------------------------------------------- V. implementation
    s += section("Implementation")
    s += [
        P("The platform is written in Python (about 7,000 lines plus tests). The stream jobs are implemented once "
          "and run in two modes: a single-process <i>lite</i> stack over an in-memory partitioned log and SQLite, "
          "used for evaluation and demonstrations, and a container stack in which each stage is a separate "
          "consumer group over Kafka with ClickHouse storage (Table II). PyFlink and Flink SQL versions of the "
          "stream jobs, a Helm chart (Strimzi, KEDA autoscaling on consumer lag, NetworkPolicies, External "
          "Secrets), Argo CD manifests, Terraform modules and CI workflows (lint, type and unit tests, schema "
          "compatibility, image scanning and signing, integration tests) are included for production deployment."),
        table("Technology stack of the container deployment", [
            ["Concern", "Choice"],
            ["Messaging", "Apache Kafka 4.3 (KRaft), Schema Registry"],
            ["Shipping", "Fluent Bit 3.1"],
            ["Analytics store", "ClickHouse 24.8 (MergeTree, TTLs)"],
            ["Object storage", "S3 API (SeaweedFS locally)"],
            ["Models", "scikit-learn, ONNX Runtime, PyTorch, MLflow 2.17"],
            ["Observability", "Prometheus, Alertmanager, Grafana"],
            ["Deployment", "Docker Compose; Helm, Argo CD, Terraform"],
        ], [0.36, 0.64]),
        P("The test suite contains 68 unit tests, two end-to-end tests that simulate traffic, train a model, inject "
          "faults and assert explained alerts, and integration tests against live Kafka and ClickHouse."),
    ]

    # ------------------------------------------------------------------------- VI. evaluation
    s += section("Evaluation")
    s += [
        subsection("Workload and Fault Injection"),
        P("Real incident labels were not available, so the evaluation uses a simulated e-commerce system of five "
          "services (gateway, orders, payments, inventory, notifications) with 2–3 hosts each. Requests form traces "
          "across services, traffic follows daily and weekly seasonality, and the logs include background noise, "
          "benign deployments and realistic personal data. A fault-injection API introduces the six labelled "
          "failure types of Table III, each of which doubles as ground truth."),
        table("Injected fault types", [
            ["Fault", "Effect in the logs"],
            ["Downstream timeout", "payment calls to the bank gateway time out; retries, 504s, latency"],
            ["Crash loop", "hosts restart repeatedly; liveness gaps, fatal exits"],
            ["Bad deploy", "a new exception after a release"],
            ["Silent service", "a service stops logging entirely"],
            ["Memory leak", "latency and GC warnings grow over two hours"],
            ["Skipped step", "orders confirmed without the payment step"],
        ], [0.33, 0.67]),
        P("Each experiment replays 7 days of normal history (about 1.2 million log lines), trains a model with the "
          "lifecycle of Section IV-E, and then replays an evaluation period with injected incidents spread over "
          "the day, including the low-traffic night hours."),
        subsection("Metrics and Baselines"),
        P("Metrics are computed per incident. An incident affects its service and, for faults that propagate, the "
          "services that call it. An alert is a true positive if it opens on an affected service between one minute "
          "before the fault and five minutes after it ends; otherwise it is a false positive. Recall is the share "
          "of incidents detected, precision is the share of alerts that are true positives, and detection delay is "
          "the time from fault start to the first matching alert. Latency is measured from the newest log event in "
          "a window to the emission of its score. The baselines are the static rule in use today (more than 50 "
          "errors in 5 min per service) and a five times more sensitive variant (more than 10), both de-duplicated "
          "like the platform’s alerts."),
        subsection("Results"),
        P("Table IV reports the main experiment (2 evaluation days, 12 incidents, two of each type) and an "
          "independent repetition with different traffic and incident schedules (3 days, 18 incidents)."),
        table("Incident-level results", [
            ["Metric", "Platform", "2nd run", "&gt;50/5m", "&gt;10/5m"],
            ["Precision", "0.91", "0.89", "–", "1.00"],
            ["Recall", "1.00", "1.00", "0.00", "0.33"],
            ["F1", "0.95", "0.94", "0.00", "0.50"],
            ["Alerts (false)", "23 (2)", "36 (4)", "0 (0)", "9 (0)"],
            ["Alerts/service/day", "2.3", "2.4", "0", "0.9"],
            ["Median delay (s)", "183", "155", "–", "284"],
            ["Latency p95 (s)", "37.6", "40.9", "–", "–"],
        ], [0.34, 0.165, 0.165, 0.165, 0.165], center_cols=(1, 2, 3, 4)),
        P("The platform detected every injected incident in both runs, at a precision above the 0.80 target and "
          "well under the budget of five alerts per service per day; the 95th-percentile latency stayed below the "
          "60 s target. The static rule in use today never fired: at the simulated traffic levels most failures "
          "do not produce 50 errors in five minutes, which illustrates the “blind at quiet times” problem. "
          "The sensitive rule was precise but caught only a third of the incidents, later than the platform. "
          "Fig. 2 breaks recall down by fault type."),
        figure(recall_chart(),
               "Recall by fault type in the main experiment (two incidents per type). Static rules see only error "
               "counts and miss silent services, slow leaks and skipped workflow steps."),
        P("The median detection delay was 2.3–4.4 min for acute faults, which reflects one-minute windows plus the "
          "two-of-three debounce. The memory leak was detected after 19.7 min on average, while latency was still "
          "climbing, by the drift detector; the skipped step was detected after 10.7 min by the sequence model, "
          "the only tier that can see it. Window-level evaluation during training, against the holdout with "
          "synthetic anomalies, gave a precision of 0.85 and recall of 0.85."),
        subsection("Live Demonstration"),
        P("The platform also runs in real time with a dashboard and a fault-injection control. In a recorded run, "
          "a bank-gateway timeout injected into the payments service raised a P1 alert for payments and P2 alerts "
          "for the dependent gateway and orders services, grouped as one incident, 138 s after injection, with a "
          "log-to-score latency of 27 s. Fig. 3 shows the explanation attached to the alert: without any rule "
          "naming the failure, the platform identified the retry and timeout templates of the bank-gateway call "
          "and attached redacted sample lines."),
        figure(screenshot("4-alert-explanation.jpg", width=0.88 * COL_W),
               "Alert detail from the live demonstration: top drivers, the templates that moved most (bank-gateway "
               "retries and timeouts), redacted sample lines and the score history around the alert."),
        subsection("Development History"),
        P("The evaluation harness was run after every change, which exposed several design errors (Table V). The "
          "first version’s drift detector was fed one-sided z-scores calibrated only for spread, so it reported "
          "a constant trend and flooded the system with false alarms; a weekly-delta feature with too little "
          "history and an out-of-range term in Tier 2 each later caused regressions. Each fix was kept only when "
          "it improved the incident-level metrics."),
        table("Incident-level precision and recall across iterations", [
            ["Change", "Precision", "Recall"],
            ["Initial ensemble", "0.10", "0.67"],
            ["Two-sided, out-of-sample drift calibration", "0.75", "1.00"],
            ["Sequence attribution; shorter idle timeout", "0.31*", "1.00"],
            ["Weekly delta removed; Tier-2 range term added", "0.29*", "0.42"],
            ["Drop Tier-2 range term; sparse-feature guard", "0.88", "1.00"],
            ["Freeze drift EWMA on acute windows", "0.91", "1.00"],
        ], [0.62, 0.19, 0.19], center_cols=(1, 2)),
        P("* Regressions: the third iteration exposed a weekly-delta baseline formed online from too few samples; "
          "the fourth was caused by the Tier-2 out-of-range term. Each was diagnosed and fixed in the next step.",
          caption),
    ]

    # ------------------------------------------------------------------------- VII. lessons
    s += section("Lessons from the Full-Stack Deployment")
    s += [
        P("The single-process stack passed all tests, yet running the 20-service container stack (Kafka, "
          "ClickHouse, object storage, MLflow with PostgreSQL, Prometheus, Alertmanager, Grafana and one container "
          "per stage) exposed fifteen defects. The most instructive were:"),
        P("<i>Write patterns.</i> The storage sink inserted one batch per poll; during a history replay this "
          "created a MergeTree part per poll and ClickHouse spent its CPU on merges until the container host "
          "failed. Buffering across polls and committing offsets only after the write raised ingestion from 127 to "
          "about 2,500 records/s. The archive sink similarly committed offsets before data reached object storage."),
        P("<i>Clock steps.</i> Kafka 4.0 and 4.1 group coordinators failed offset commits (“histogram recorded "
          "value cannot be negative”) when the virtual machine’s clock stepped backwards after host sleep, which "
          "stalled every consumer group. Inspecting the coordinator bytecode showed that 4.3.0 clamps the value, and "
          "upgrading resolved it. A health check that launched a JVM every 10 s starved the single broker until it "
          "was fenced by its own controller, which in turn left idempotent producers in a fatal state."),
        P("<i>Operational semantics.</i> Alertmanager expires alerts that are not re-sent, so the router must "
          "re-send open alerts periodically; a router restart must restore open alerts to avoid duplicates; and a "
          "dead-man’s-switch rule must aggregate across stages. None of these appear without the real components."),
        P("A further operational lesson concerns resources: on a laptop virtual machine with 3.7 GB of memory, "
          "training alongside the full stack exhausted memory, which supports the proposal’s request for a "
          "dedicated server (8+ vCPU, 32 GB) for staging."),
    ]

    # ------------------------------------------------------------------------- VIII. limitations
    s += section("Limitations and Threats to Validity")
    s += [
        P("The workload is simulated. Although it includes seasonality, noise, benign deployments and realistic "
          "message formats, real services exhibit more template diversity and messier failures, and incidents "
          "overlap. The evaluation covers 12 and 18 incidents, so per-fault estimates rest on two or three cases "
          "each. The sequence model was not yet validated on the public HDFS and BGL benchmarks [6], for which "
          "a harness is provided. The Flink jobs, the Kubernetes deployment and the CI workflows were written "
          "against documented interfaces but not executed, and external alert channels (Slack, PagerDuty) were not "
          "connected. Finally, the static baselines are simple; a comparison with tuned per-service thresholds or "
          "a commercial tool would strengthen the conclusions."),
    ]

    # ------------------------------------------------------------------------- IX. conclusion
    s += section("Conclusion and Future Work")
    s += [
        P("We designed, implemented and evaluated a streaming log anomaly detection platform that learns normal "
          "behaviour per service and hour, combines statistical, isolation-forest and sequence models with hard "
          "rules, and delivers few, grouped and explained alerts. On labelled fault injection it met every target "
          "of the project proposal—precision 0.91, recall 1.00, 2.3 alerts per service per day and a "
          "95th-percentile latency of 37.6 s—where the static rule in use today detected nothing. Next steps are an "
          "evaluation on an anonymised sample of real logs, validation of the sequence model on HDFS and BGL, "
          "deployment of the Flink jobs and Helm chart to a staging cluster with load and chaos tests at twice the "
          "target rate, and, from accumulated feedback, a supervised re-ranker and LLM-generated incident "
          "summaries."),
    ]

    # ------------------------------------------------------------------------- references
    s += [CondPageBreak(1.2 * inch), P("REFERENCES", h1)]
    refs = [
        "P. He, J. Zhu, Z. Zheng, and M. R. Lyu, “Drain: An online log parsing approach with fixed depth tree,” in "
        "<i>Proc. IEEE Int. Conf. Web Services (ICWS)</i>, 2017, pp. 33–40.",
        "M. Du, F. Li, G. Zheng, and V. Srikumar, “DeepLog: Anomaly detection and diagnosis from system logs "
        "through deep learning,” in <i>Proc. ACM SIGSAC Conf. Computer and Communications Security (CCS)</i>, "
        "2017, pp. 1285–1298.",
        "F. T. Liu, K. M. Ting, and Z.-H. Zhou, “Isolation forest,” in <i>Proc. IEEE Int. Conf. Data Mining "
        "(ICDM)</i>, 2008, pp. 413–422.",
        "S. He, J. Zhu, P. He, and M. R. Lyu, “Experience report: System log analysis for anomaly detection,” in "
        "<i>Proc. IEEE Int. Symp. Software Reliability Engineering (ISSRE)</i>, 2016, pp. 207–218.",
        "W. Xu, L. Huang, A. Fox, D. Patterson, and M. I. Jordan, “Detecting large-scale system problems by mining "
        "console logs,” in <i>Proc. ACM Symp. Operating Systems Principles (SOSP)</i>, 2009, pp. 117–132.",
        "J. Zhu, S. He, J. Liu, P. He, Q. Xie, Z. Zheng, and M. R. Lyu, “Tools and benchmarks for automated log "
        "parsing,” in <i>Proc. IEEE/ACM Int. Conf. Software Engineering: Software Engineering in Practice "
        "(ICSE-SEIP)</i>, 2019, pp. 121–130.",
        "J. Kreps, N. Narkhede, and J. Rao, “Kafka: A distributed messaging system for log processing,” in "
        "<i>Proc. NetDB Workshop</i>, 2011.",
        "P. Carbone, A. Katsifodimos, S. Ewen, V. Markl, S. Haridi, and K. Tzoumas, “Apache Flink: Stream and "
        "batch processing in a single engine,” <i>IEEE Data Eng. Bull.</i>, vol. 38, no. 4, pp. 28–38, 2015.",
        "P. J. Rousseeuw and C. Croux, “Alternatives to the median absolute deviation,” <i>J. Amer. Statist. "
        "Assoc.</i>, vol. 88, no. 424, pp. 1273–1283, 1993.",
        "S. He, P. He, Z. Chen, T. Yang, Y. Su, and M. R. Lyu, “A survey on automated log analysis for "
        "reliability engineering,” <i>ACM Comput. Surv.</i>, vol. 54, no. 6, pp. 1–37, 2021.",
        "B. Beyer, C. Jones, J. Petoff, and N. R. Murphy, Eds., <i>Site Reliability Engineering: How Google Runs "
        "Production Systems</i>. Sebastopol, CA, USA: O’Reilly Media, 2016.",
        "T. Akidau <i>et al.</i>, “The dataflow model: A practical approach to balancing correctness, latency, and "
        "cost in massive-scale, unbounded, out-of-order data processing,” <i>Proc. VLDB Endow.</i>, vol. 8, "
        "no. 12, pp. 1792–1803, 2015.",
        "M. Zaharia <i>et al.</i>, “Accelerating the machine learning lifecycle with MLflow,” <i>IEEE Data Eng. "
        "Bull.</i>, vol. 41, no. 4, pp. 39–45, 2018.",
        "J. Lin, “Divergence measures based on the Shannon entropy,” <i>IEEE Trans. Inf. Theory</i>, vol. 37, "
        "no. 1, pp. 145–151, 1991.",
    ]
    for i, r in enumerate(refs, 1):
        s.append(P(f"[{i}]&nbsp;&nbsp;{r}", ref))

    doc.build(s)
    return OUT


if __name__ == "__main__":
    print(build())
