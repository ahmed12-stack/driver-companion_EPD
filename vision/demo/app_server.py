"""Driver Companion trip app: your trips, a speed trace for each one, every
overspeed moment, the trip summary, and "Ask" - questions about your driving
answered by the on-device language model (llm.py).

On the real device the Pi serves this over its own password-protected Wi-Fi
hotspot (no internet). In the prototype it runs on the laptop: open it in the
laptop's browser, or on a phone on the same Wi-Fi. No web fonts or outside
scripts are loaded, so it works fully offline.

    python demo/app_server.py                 # reads demo/trips.db, port 5000
    (webcam_demo.py also starts it automatically)
"""
import argparse
import json
import socket
import sqlite3
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request
from markupsafe import Markup

import llm

DEFAULT_DB = Path(__file__).resolve().parent / "trips.db"
MARGIN = 5          # km/h over the limit that counts as overspeed (REQ-05)


# ----------------------------------------------------------------- words
def nice_label(label):
    if not label:
        return ""
    if label.startswith("speed_limit_"):
        return f"Speed limit {label.split('_')[-1]}"
    return label.replace("_", " ").capitalize()


def clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def clock_s(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def part_of_day(ts):
    h = time.localtime(ts).tm_hour
    return ("Night" if h < 5 else "Morning" if h < 12 else "Afternoon" if h < 17
            else "Evening" if h < 21 else "Night")


def trip_title(ts):
    """'This afternoon', 'Yesterday evening', 'Monday morning'."""
    day, part = day_name(ts), part_of_day(ts).lower()
    if day == "Today":
        return "Tonight" if part == "night" else f"This {part}"
    return f"{day} {part}"


def day_name(ts):
    d = time.localtime(ts)
    today = time.localtime()
    if d.tm_yday == today.tm_yday and d.tm_year == today.tm_year:
        return "Today"
    if (time.mktime(today) - time.mktime(d)) < 2 * 86400 and today.tm_yday - d.tm_yday == 1:
        return "Yesterday"
    return time.strftime("%A", d)


def duration_words(a, b):
    s = max(0, (b or time.time()) - a)
    if s < 60:
        return f"{int(s)} seconds"
    m = round(s / 60)
    return f"{m} minute{'s' if m != 1 else ''}" if m < 90 else f"{s / 3600:.1f} hours"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))          # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ----------------------------------------------------------------- speed trace
def trace_svg(samples, w, h, pad_l=0, pad_b=0, axes=False, draw_in=False):
    """The speed trace as inline SVG. Blue line, red where more than MARGIN over
    the limit, the limit as a quiet stepped line. The SVG stretches to any width, so
    its text labels are returned separately and placed as HTML on top.
    Returns (svg markup, points json, labels html)."""
    if len(samples) < 2:
        return None, "[]", ""
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    span = max(1.0, t1 - t0)
    top = max(max(s["speed"] for s in samples), max((s["limit_kmh"] or 0) for s in samples))
    top = max(40, int((top + 19) // 20) * 20)          # round up to a clean 20
    pw, ph = w - pad_l - (12 if axes else 2), h - pad_b - (10 if axes else 2)
    X = lambda t: pad_l + (t - t0) / span * pw
    Y = lambda v: (10 if axes else 1) + ph - v / top * ph

    parts, labels = [], []

    def label(x, y, text, cls, anchor="start"):
        shift = {"start": "0", "middle": "-50%", "end": "-100%"}[anchor]
        labels.append(f'<span class="{cls}" style="left:{x / w * 100:.2f}%;top:{y / h * 100:.2f}%;'
                      f'transform:translate({shift},-50%)">{text}</span>')
    if axes:                                           # hairline grid + y labels
        for v in range(0, top + 1, 20 if top <= 120 else 40):
            y = Y(v)
            parts.append(f'<line class="grid" x1="{pad_l}" x2="{w - 12}" y1="{y:.1f}" y2="{y:.1f}"/>')
            label(pad_l - 8, y, v, "tick", "end")
        mins = span / 60
        step = 1 if mins <= 6 else 2 if mins <= 12 else 5 if mins <= 30 else 10 if mins <= 60 else 30
        m = step
        while m * 60 <= span + 1:
            x = X(t0 + m * 60)
            label(x, h - 8, f"{m} min", "tick", "middle")
            m += step

    # limit, as a stepped line, only where a limit is known
    seg, labelled = [], set()
    for s in samples + [{"t": t1 + 1, "limit_kmh": None}]:
        lim = s.get("limit_kmh")
        if seg and lim != seg[0][1]:
            if seg[0][1]:
                xa, xb, y = X(seg[0][0]), X(min(s["t"], t1)), Y(seg[0][1])
                parts.append(f'<line class="limit" x1="{xa:.1f}" x2="{xb:.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
                if axes and xb - xa > 70 and seg[0][1] not in labelled:
                    labelled.add(seg[0][1])
                    label(xb - 4, y + 11, f"Limit {seg[0][1]}", "limit-label", "end")
            seg = []
        seg.append((s["t"], lim))

    # speed line, split into normal and over-limit runs
    def over(s):
        return s["limit_kmh"] is not None and s["speed"] > s["limit_kmh"] + MARGIN
    runs, cur = [], [samples[0]]
    for a, b in zip(samples, samples[1:]):
        if over(b) != over(cur[-1]) and len(cur) > 0:
            cur.append(b)
            runs.append((over(a), cur))
            cur = [b]
        else:
            cur.append(b)
    runs.append((over(cur[-1]), cur))
    paths = []
    for is_over, pts in runs:
        d = "M" + " L".join(f"{X(p['t']):.1f},{Y(p['speed']):.1f}" for p in pts)
        paths.append(f'<path class="speed{" over" if is_over else ""}" d="{d}" pathLength="1"/>')
    cls = "trace draw" if draw_in else "trace"
    svg = (f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
           f'role="img" aria-label="Speed over the trip">{"".join(parts)}{"".join(paths)}</svg>')
    pts = [{"x": round((X(s["t"]) / w) * 100, 2), "y": round(Y(s["speed"]) / h * 100, 2),
            "t": clock_s(s["t"]), "v": round(s["speed"]), "l": s["limit_kmh"]} for s in samples]
    return Markup(svg), json.dumps(pts), Markup("".join(labels))


def thin(samples, n):
    if len(samples) <= n:
        return samples
    step = len(samples) / n
    return [samples[int(i * step)] for i in range(n)] + [samples[-1]]


# ----------------------------------------------------------------- page shell
CSS = """
:root{--page:#f5f5f7;--surface:#fff;--ink:#1d1d1f;--ink2:#6e6e73;--line:#d2d2d7;
--blue:#0a6cdb;--red:#e0352b;--amber:#b25e00;--green:#248a3d;--grid:#e8e8ed;--nav:rgba(245,245,247,.72)}
@media (prefers-color-scheme:dark){:root{--page:#000;--surface:#1c1c1e;--ink:#f5f5f7;--ink2:#a1a1a6;
--line:#38383a;--blue:#3d8df5;--red:#f2564b;--amber:#f0a03c;--green:#4cc764;--grid:#2c2c2e;--nav:rgba(0,0,0,.72)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);
font:17px/1.47 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI Variable Text","Segoe UI",Roboto,"Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased;letter-spacing:-.01em}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:3px solid var(--blue);outline-offset:3px;border-radius:6px}
nav{position:sticky;top:0;z-index:10;background:var(--nav);backdrop-filter:saturate(180%) blur(20px);
-webkit-backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid var(--line)}
nav .in{max-width:980px;margin:0 auto;height:48px;padding:0 22px;display:flex;align-items:center;gap:28px}
nav .brand{font-weight:600;color:var(--ink);margin-right:auto;letter-spacing:-.02em}
nav a{font-size:14px;color:var(--ink2)} nav a.on{color:var(--ink)}
main{max-width:980px;margin:0 auto;padding:0 22px 96px}
.hero{padding:64px 0 40px}
h1{font:600 clamp(36px,6vw,56px)/1.07 -apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI Variable Display","Segoe UI",Roboto,sans-serif;
letter-spacing:-.03em;margin:0;max-width:16ch}
.sub{font-size:21px;line-height:1.38;color:var(--ink2);margin:14px 0 0;max-width:40ch;letter-spacing:-.015em}
h2{font:600 28px/1.14 -apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI Variable Display","Segoe UI",Roboto,sans-serif;
letter-spacing:-.02em;margin:56px 0 16px}
.muted{color:var(--ink2)} .small{font-size:14px}
.back{display:inline-flex;align-items:center;gap:4px;font-size:15px;margin-top:28px}
.back svg{width:9px;height:14px}
.panel{background:var(--surface);border-radius:18px;padding:28px 28px 22px}
/* trips list */
.trips{background:var(--surface);border-radius:18px;padding:0 24px}
.trip{display:grid;grid-template-columns:1fr minmax(90px,180px) 110px;align-items:center;gap:20px;
padding:18px 0;border-top:1px solid var(--line);color:inherit}
.trip:first-child{border-top:0}
.trip:hover{text-decoration:none} .trip:hover .when{color:var(--blue)}
.when{font-weight:600} .meta{font-size:14px;color:var(--ink2);margin-top:2px}
.spark{width:100%;height:34px}
.status{font-size:14px;text-align:right;color:var(--ink2);white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:1px}
.d-red{background:var(--red)} .d-green{background:var(--green)} .d-blue{background:var(--blue)}
.d-amber{background:var(--amber)} .d-grey{background:var(--ink2)}
.live .dot{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
/* summary */
.summary{font-size:24px;line-height:1.33;letter-spacing:-.02em;margin:0;max-width:34ch}
.provenance{margin-top:14px;font-size:14px;color:var(--ink2);display:flex;align-items:center;gap:6px}
.provenance svg{width:15px;height:15px;flex:none}
/* chart */
.chart{position:relative;margin-top:8px}
.trace{width:100%;height:280px;display:block;overflow:visible}
.spark .trace,.spark{height:34px}
.trace .grid{stroke:var(--grid);stroke-width:1;vector-effect:non-scaling-stroke}
.labels span{position:absolute;font-size:12px;color:var(--ink2);white-space:nowrap;pointer-events:none;font-variant-numeric:tabular-nums}
.trace .limit{stroke:var(--ink2);stroke-width:1.5;opacity:.55;vector-effect:non-scaling-stroke}
.trace .speed{fill:none;stroke:var(--blue);stroke-width:2;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
.trace .speed.over{stroke:var(--red);stroke-width:2.5}
.trace.draw .speed{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.4s cubic-bezier(.4,0,.2,1) .15s forwards}
@keyframes draw{to{stroke-dashoffset:0}}
@media (prefers-reduced-motion:reduce){.trace.draw .speed{animation:none;stroke-dashoffset:0}.live .dot{animation:none}}
.key{display:flex;gap:8px 22px;font-size:14px;color:var(--ink2);margin-top:12px;flex-wrap:wrap}
.key i{display:inline-block;width:18px;height:0;border-top:2px solid;vertical-align:4px;margin-right:7px}
.cross{position:absolute;top:0;bottom:22px;width:1px;background:var(--ink2);opacity:0;pointer-events:none}
.tip{position:absolute;top:-6px;transform:translateX(-50%);background:var(--surface);color:var(--ink);
border:1px solid var(--line);border-radius:10px;padding:6px 10px;font-size:13px;line-height:1.35;
pointer-events:none;opacity:0;white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.08)}
.hit{position:absolute;inset:0 0 22px 0;cursor:crosshair}
/* figures */
.figs{display:grid;grid-template-columns:repeat(4,1fr);margin-top:8px}
.fig{padding:4px 20px 4px 0;border-left:1px solid var(--line);padding-left:20px}
.fig:first-child{border-left:0;padding-left:0}
.fig b{display:block;font:600 34px/1.1 -apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI Variable Display","Segoe UI",sans-serif;
letter-spacing:-.025em;font-variant-numeric:tabular-nums}
.fig span{font-size:14px;color:var(--ink2)}
/* moments */
.moments{list-style:none;margin:0;padding:0}
.moments li{display:grid;grid-template-columns:64px 1fr;gap:16px;padding:13px 0;border-top:1px solid var(--line)}
.moments li:first-child{border-top:0}
.moments time{color:var(--ink2);font-variant-numeric:tabular-nums;font-size:15px}
.over-row{display:grid;grid-template-columns:64px 1fr auto;gap:16px;padding:14px 0;border-top:1px solid var(--line);align-items:baseline}
.over-row:first-child{border-top:0}
.over-row .big{font-weight:600;font-variant-numeric:tabular-nums}
/* ask */
.askbox{display:flex;gap:10px;background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:6px 6px 6px 22px;max-width:640px}
.askbox input{flex:1;border:0;background:transparent;color:var(--ink);font:inherit;outline:none;min-width:0}
.askbox button{border:0;border-radius:999px;background:var(--blue);color:#fff;font:600 15px/1 inherit;padding:11px 18px;cursor:pointer}
.askbox button:disabled{opacity:.5;cursor:default}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 0}
.chips button{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
padding:8px 15px;font:inherit;font-size:15px;cursor:pointer}
.chips button:hover{border-color:var(--ink2)}
.qa{margin-top:40px;max-width:640px}
.qa .q{font-weight:600;margin-top:30px}
.qa .a{font-size:21px;line-height:1.38;letter-spacing:-.015em;margin-top:6px}
.empty{padding:40px 0;color:var(--ink2);font-size:19px;max-width:36ch}
@media (max-width:640px){
.hero{padding:40px 0 28px}
.trip{grid-template-columns:1fr 92px;gap:12px}.trip .spark{display:none}
.figs{grid-template-columns:1fr 1fr;row-gap:22px}.fig:nth-child(3){border-left:0;padding-left:0}
.panel{padding:22px 18px 16px}.trips{padding:0 18px}.summary{font-size:21px}
.trace{height:220px}
.over-row{grid-template-columns:52px 1fr}.over-row>div:last-child{grid-column:2;margin-top:-10px;font-size:14px}}
"""

SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
{% if refresh %}<meta http-equiv="refresh" content="{{ refresh }}">{% endif %}
<title>{{ title }} - Driver Companion</title><style>{{ css }}</style></head><body>
<nav><div class="in"><a class="brand" href="/">Driver Companion</a>
<a href="/" class="{{ 'on' if tab=='trips' }}">Trips</a><a href="/ask" class="{{ 'on' if tab=='ask' }}">Ask</a></div></nav>
<main>{{ body }}</main></body></html>"""

CHECK = ('<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7.25" fill="none" '
         'stroke="currentColor" stroke-width="1.5"/><path d="M4.8 8.3l2.1 2.1 4.3-4.6" fill="none" '
         'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')

TRIPS_BODY = """
<section class="hero"><h1>{{ headline }}</h1>{% if subline %}<p class="sub">{{ subline }}</p>{% endif %}</section>
{% if trips %}
<div class="trips">
{% for t in trips %}
<a class="trip{{ ' live' if t.live }}" href="/trip/{{ t.id }}">
  <div><div class="when">{{ t.day }}, {{ t.clock }}</div>
  <div class="meta">{{ "%.1f"|format(t.distance_km) }} km in {{ t.duration }}</div></div>
  <div class="spark">{{ t.spark or '' }}</div>
  <div class="status">{% if t.live %}<span class="dot d-blue"></span>Driving now
  {% elif t.interrupted %}<span class="dot d-grey"></span>Not finished
  {% elif t.overspeed_count %}<span class="dot d-red"></span>{{ t.overspeed_count }} over limit
  {% else %}<span class="dot d-green"></span>Within limits{% endif %}</div>
</a>
{% endfor %}
</div>
<p class="muted small" style="margin-top:18px">The last 20 trips are kept on the device. Nothing leaves it.</p>
{% else %}
<p class="empty">No trips yet. Start the camera demo and drive one; it will appear here as you go.</p>
{% endif %}"""

TRIP_BODY = """
<a class="back" href="/"><svg viewBox="0 0 9 14" aria-hidden="true"><path d="M7.5 1.5L2 7l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>Trips</a>
<section class="hero" style="padding-top:28px"><h1>{{ t.title }}</h1>
<p class="sub">{{ "%.1f"|format(t.distance_km) }} km in {{ t.duration }}, starting {{ t.clock }}.{% if t.live %} Still driving; this page updates every 5 seconds.{% endif %}</p></section>

<div class="panel">
{% if t.summary %}<p class="summary">{{ t.summary }}</p>
<div class="provenance">{% if t.summary_source == 'llm' and t.summary_verified %}{{ check }}Written by the on-device model. Every number checked against the trip data.
{% else %}Written from the trip data.{% endif %}</div>
{% else %}<p class="summary muted">The summary is written when the trip ends.</p>{% endif %}
</div>

<h2>Speed</h2>
<div class="panel">
{% if trace %}
<div class="chart" id="chart">{{ trace }}<div class="labels">{{ labels }}</div><div class="cross" id="cross"></div><div class="tip" id="tip"></div><div class="hit" id="hit"></div></div>
<div class="key"><span><i style="color:var(--blue)"></i>Your speed</span><span><i style="color:var(--red)"></i>More than {{ margin }} km/h over the limit</span><span><i style="color:var(--ink2);opacity:.55"></i>Speed limit from the last sign</span></div>
<script>
(function(){var P={{ points|safe }},c=document.getElementById('chart'),h=document.getElementById('hit'),
x=document.getElementById('cross'),tp=document.getElementById('tip');
function show(e){var r=h.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*100,b=P[0],d=1e9;
for(var i=0;i<P.length;i++){var k=Math.abs(P[i].x-px);if(k<d){d=k;b=P[i];}}
x.style.left=b.x+'%';x.style.opacity=.5;tp.style.left=Math.min(88,Math.max(12,b.x))+'%';tp.style.opacity=1;
tp.innerHTML='<b>'+b.v+' km/h</b><br>'+(b.l?'Limit '+b.l+'<br>':'')+b.t;}
function hide(){x.style.opacity=0;tp.style.opacity=0;}
h.addEventListener('mousemove',show);h.addEventListener('mouseleave',hide);
h.addEventListener('touchstart',function(e){show(e.touches[0]);},{passive:true});
h.addEventListener('touchmove',function(e){show(e.touches[0]);},{passive:true});})();
</script>
{% else %}<p class="muted" style="margin:0">No speed data was recorded for this trip.</p>{% endif %}
<div class="figs" style="margin-top:26px">
 <div class="fig"><b>{{ "%.0f"|format(t.max_speed) }}</b><span>Top speed, km/h</span></div>
 <div class="fig"><b>{{ "%.0f"|format(t.avg_speed) }}</b><span>Average speed, km/h</span></div>
 <div class="fig"><b>{{ "%.0f"|format(t.over_s) }} s</b><span>Over the limit</span></div>
 <div class="fig"><b>{{ t.harsh_brakes }}</b><span>Harsh braking</span></div>
</div>
</div>

{% if overspeed %}
<h2>Over the limit</h2>
<div class="panel" style="padding-top:10px;padding-bottom:10px">
{% for e in overspeed %}
<div class="over-row"><time class="muted">{{ e.clock }}</time>
<div><span class="dot d-red"></span><span class="big">{{ "%.0f"|format(e.speed) }} km/h</span> in a {{ e.limit_kmh }} zone</div>
<div class="muted">{{ "%.0f"|format(e.duration_s) }} s, {{ "%.0f"|format(e.speed - e.limit_kmh) }} over</div></div>
{% endfor %}
</div>
{% endif %}

<h2>Along the way</h2>
<div class="panel" style="padding-top:8px;padding-bottom:8px">
{% if moments %}<ul class="moments">
{% for e in moments %}<li><time>{{ e.clock }}</time><div><span class="dot {{ e.dot }}"></span>{{ e.text }}</div></li>{% endfor %}
</ul>{% else %}<p class="muted">Nothing happened on this trip yet.</p>{% endif %}
</div>"""

ASK_BODY = """
<section class="hero"><h1>Ask about your driving.</h1>
<p class="sub">Answers come from your last 20 trips, worked out on this device. {{ model_line }}</p></section>
<form class="askbox" id="f" autocomplete="off"><input id="q" name="q" placeholder="How was my last trip?" aria-label="Your question" maxlength="300">
<button id="go" type="submit">Ask</button></form>
<div class="chips" id="chips">
<button type="button">How was my last trip?</button>
<button type="button">When did I go over the limit?</button>
<button type="button">How many times did I brake hard?</button>
<button type="button">Which trip was my safest?</button></div>
<div class="qa" id="qa" aria-live="polite"></div>
<script>
(function(){var f=document.getElementById('f'),q=document.getElementById('q'),go=document.getElementById('go'),qa=document.getElementById('qa');
function esc(s){return s.replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function ask(text){if(!text.trim())return;go.disabled=true;
var b=document.createElement('div');b.innerHTML='<div class="q">'+esc(text)+'</div><div class="a muted">Thinking…</div>';qa.prepend(b);
fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text})})
.then(function(r){return r.json();}).then(function(d){var a=b.querySelector('.a');a.classList.remove('muted');a.textContent=d.answer;
var p=document.createElement('div');p.className='provenance';
p.innerHTML=d.source==='llm'?'{{ check|safe }}Checked against your trip data.':'Taken straight from your trip data.';b.appendChild(p);})
.catch(function(){b.querySelector('.a').textContent='The app could not reach the device. Check you are on its Wi-Fi and try again.';})
.finally(function(){go.disabled=false;});}
f.addEventListener('submit',function(e){e.preventDefault();ask(q.value);q.value='';});
document.getElementById('chips').addEventListener('click',function(e){if(e.target.tagName==='BUTTON')ask(e.target.textContent);});})();
</script>"""


def describe(e):
    k = e["kind"]
    if k == "sign":
        return f"{nice_label(e['label'])} sign", "d-blue"
    if k == "warning":
        return f"Warned to slow down at {e['speed']:.0f} km/h in a {e['limit_kmh']} zone", "d-red"
    if k == "overspeed":
        return (f"Over the limit for {e['duration_s']:.0f} s, up to {e['speed']:.0f} km/h "
                f"in a {e['limit_kmh']} zone"), "d-red"
    if k == "harsh_brake":
        return f"Harsh braking, {e['label'].replace('->', ' to ')}", "d-amber"
    if k == "harsh_accel":
        return f"Hard acceleration, {e['label'].replace('->', ' to ')}", "d-amber"
    if k == "camera":
        return ("Camera blocked, sign alerts paused" if e["label"] == "blinded"
                else "Camera clear, sign alerts back on"), "d-grey"
    if k == "limit_expired":
        return f"Speed limit {e['limit_kmh']} ended after 5 km", "d-grey"
    return k, "d-grey"


# ----------------------------------------------------------------- app
def create_app(db_path=DEFAULT_DB, model=None):
    app = Flask(__name__)
    app.json.sort_keys = False
    model = model or llm.LocalLLM()

    def db():
        con = sqlite3.connect(str(db_path), timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def page(body, title, tab="trips", refresh=None, **ctx):
        inner = render_template_string(body, check=Markup(CHECK), margin=MARGIN, **ctx)
        return render_template_string(SHELL, body=Markup(inner), title=title, tab=tab,
                                      css=Markup(CSS), refresh=refresh)

    def last_activity(con, trip_id):
        try:
            return con.execute("SELECT MAX(t) FROM samples WHERE trip_id=?", (trip_id,)).fetchone()[0]
        except sqlite3.OperationalError:
            return None

    def trip_dict(r, con):
        d = dict(r)
        d.setdefault("summary_source", "template")
        d.setdefault("summary_verified", 0)
        # "Driving now" only if the trip is unfinished AND got data in the last 20 s;
        # an unfinished trip that went quiet was interrupted (closed by the next start).
        last = last_activity(con, r["id"]) or r["started_at"]
        d["live"] = r["ended_at"] is None and time.time() - last < 20
        d["interrupted"] = r["ended_at"] is None and not d["live"]
        d["day"] = day_name(r["started_at"])
        d["part"] = part_of_day(r["started_at"])
        d["title"] = trip_title(r["started_at"])
        d["clock"] = clock(r["started_at"])
        d["duration"] = duration_words(r["started_at"], r["ended_at"])
        return d

    def samples(con, trip_id):
        try:
            return [dict(s) for s in con.execute(
                "SELECT t, speed, limit_kmh FROM samples WHERE trip_id=? ORDER BY t", (trip_id,))]
        except sqlite3.OperationalError:                 # database from before samples existed
            return []

    def load_trips():
        try:
            with db() as con:
                rows = con.execute("SELECT * FROM trips ORDER BY started_at DESC LIMIT 20").fetchall()
                out = []
                for r in rows:
                    t = trip_dict(r, con)
                    t["spark"], _, _ = trace_svg(thin(samples(con, r["id"]), 80), 180, 34)
                    out.append(t)
                return out
        except sqlite3.OperationalError:                 # database not created yet
            return []

    def load_trip(trip_id):
        with db() as con:
            r = con.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
            if r is None:
                abort(404)
            evs = [dict(e) for e in con.execute(
                "SELECT * FROM events WHERE trip_id=? ORDER BY t", (trip_id,))]
            smp = samples(con, trip_id)
            t = trip_dict(r, con)
        for e in evs:
            e["clock"] = clock(e["t"])
            e["text"], e["dot"] = describe(e)
        return t, evs, smp

    @app.route("/")
    def trips():
        ts = load_trips()
        done = [t for t in ts if not t["live"]]
        clean = sum(1 for t in done if not t["overspeed_count"])
        if not ts:
            headline, sub = "Your drives, in one place.", None
        elif any(t["live"] for t in ts) and not done:
            headline, sub = "Driving now.", "This trip is being recorded. Open it to watch it live."
        else:
            n = len(done)
            headline = (f"{n} trip{'s' if n != 1 else ''}, {clean} within the limit."
                        if n else "Your first trip is on its way.")
            total = sum(t["distance_km"] for t in done)
            sub = f"{total:.1f} km recorded so far." if n else None
        live = any(t["live"] for t in ts)
        return page(TRIPS_BODY, "Trips", trips=ts, headline=headline, subline=sub,
                    refresh=5 if live else None)

    @app.route("/trip/<int:trip_id>")
    def trip(trip_id):
        t, evs, smp = load_trip(trip_id)
        trace, points, labels = trace_svg(thin(smp, 600), 920, 280, pad_l=34, pad_b=22, axes=True,
                                  draw_in=not t["live"])
        over = [e for e in evs if e["kind"] == "overspeed"]
        moments = [e for e in evs if e["kind"] not in ("overspeed", "warning")]
        moments += over
        moments.sort(key=lambda e: e["t"])
        return page(TRIP_BODY, t["title"], t=t, trace=trace,
                    points=Markup(points), labels=labels, overspeed=over, moments=moments,
                    refresh=5 if t["live"] else None)

    @app.route("/ask")
    def ask_page():
        up = model.available()
        line = (f"The model ({model.model}) is running." if up else
                "The language model isn't running right now, so answers are basic.")
        return page(ASK_BODY, "Ask", tab="ask", model_line=line)

    @app.route("/api/ask", methods=["POST"])
    def api_ask():
        q = (request.get_json(silent=True) or {}).get("question", "")
        if not q.strip():
            return jsonify({"answer": "Type a question first.", "source": "app"}), 400
        with db() as con:
            r = llm.answer_question(con, q, model)
        return jsonify({"answer": r["answer"], "source": r["source"], "verified": r["verified"]})

    @app.route("/api/trips")
    def api_trips():
        return jsonify([{k: v for k, v in t.items() if k != "spark"} for t in load_trips()])

    @app.route("/api/trip/<int:trip_id>")
    def api_trip(trip_id):
        t, evs, smp = load_trip(trip_id)
        return jsonify({"trip": t, "events": evs, "samples": smp})

    return app


def serve_in_background(db_path, port=5000):
    """Start the app on a background thread (used by webcam_demo.py)."""
    import logging
    import threading
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app = create_app(db_path)
    th = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False,
                                                 use_reloader=False, threaded=True),
                          daemon=True, name="app-server")
    th.start()
    return f"http://{lan_ip()}:{port}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Driver Companion trip app")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    print(f"Open on this laptop:  http://127.0.0.1:{a.port}")
    print(f"Open on your phone:   http://{lan_ip()}:{a.port}  (same Wi-Fi)")
    create_app(a.db).run(host="0.0.0.0", port=a.port, debug=False)
