"""Plot the autoscaling test from hpa-timeline.txt.

Usage (matplotlib is only needed for this script, it is not an app dependency):
    pip install matplotlib
    python test-results/plot_hpa.py
"""
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
SRC = HERE / "hpa-timeline.txt"
OUT = HERE / "hpa-autoscaling.png"

times, cpu, pods = [], [], []
current = None
for line in SRC.read_text().splitlines():
    m = re.match(r"== (\d\d:\d\d:\d\d)", line)
    if m:
        current = {"t": datetime.strptime(m.group(1), "%H:%M:%S"), "cpu": None, "pods": 0}
        times.append(current["t"])
        cpu.append(None)
        pods.append(0)
        continue
    if current is None:
        continue
    m = re.search(r"cpu: (\d+)%/70%", line)
    if m:
        cpu[-1] = int(m.group(1))
    elif line.startswith("   flask-app-") and "Running" in line:
        pods[-1] += 1

rows = [(t, c, p) for t, c, p in zip(times, cpu, pods) if c is not None]
t, c, p = zip(*rows)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)

ax1.plot(t, c, color="tab:red", linewidth=2)
ax1.axhline(70, color="black", linestyle="--", linewidth=1)
ax1.text(t[0], 74, "HPA target: 70% of CPU request", fontsize=9)
ax1.set_ylabel("Avg CPU (% of request)")
ax1.set_title("Flask HPA under load (min 2, max 5 replicas)")
ax1.grid(alpha=0.3)

ax2.step(t, p, where="post", color="tab:blue", linewidth=2)
ax2.axhline(5, color="gray", linestyle=":", linewidth=1)
ax2.text(t[0], 5.1, "maxReplicas = 5", fontsize=9)
ax2.set_ylabel("Running flask-app pods")
ax2.set_ylim(0, 6.2)
ax2.set_yticks(range(0, 7))
ax2.set_xlabel("Time (host clock, HH:MM)")
ax2.grid(alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

def mark(ax, label, when):
    ts = datetime.strptime(when, "%H:%M:%S")
    for a in (ax1, ax2):
        a.axvline(ts, color="green", linestyle="-", alpha=0.35)
    ax.text(ts, ax.get_ylim()[1] * 0.92, " " + label, fontsize=9, color="green")

mark(ax1, "load applied", "16:17:56")
mark(ax1, "load removed (approx.)", "16:22:30")

fig.text(0.01, 0.005, "Generated from test-results/hpa-timeline.txt (kubectl snapshots every 15 s)",
         fontsize=8, color="gray")
fig.tight_layout(rect=(0, 0.02, 1, 1))
fig.savefig(OUT, dpi=130)
print("saved", OUT, "| points:", len(rows), "| max cpu:", max(c), "| max pods:", max(p), "| min pods:", min(p))
