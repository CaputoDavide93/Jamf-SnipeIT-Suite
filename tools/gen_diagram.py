#!/usr/bin/env python3
"""Draw every diagram in this repository as SVG, one file per colour scheme.

  docs/assets/<name>-light.svg
  docs/assets/<name>-dark.svg

The README used to carry these as Mermaid, which GitHub renders on its own
terms: it picks the theme, pins the version, ignores `%%{init}%%` and decodes
HTML entities before parsing. Drawn here, the picture is exactly what is
committed.

**GitHub sanitises SVG in markdown**, so no <style>, no <script>, no web font
and no <foreignObject>. Everything is a presentation attribute and the type is
a system stack. Each pair is served from one <picture>, which GitHub switches
on prefers-color-scheme.

Layout is explicit rather than solved: these diagrams are small enough that
placing them by hand is cheaper than a layout engine nobody can predict.

House rules: a slate scale, a single accent on the one thing that matters in
each picture, drawn icons rather than emoji, monospace for anything that is
literally typed, and text contrast at or above 4.5:1 in both schemes.

Run `python tools/gen_diagram.py` after editing; tests/test_diagrams.py fails
if a committed SVG differs from what this file draws.
"""
from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parents[1]
SANS = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"

SCHEMES = {
    "light": dict(card="#ffffff", border="#d8dee4", title="#0f172a", sub="#5b6673",
                  accent="#2b59c3", on_accent="#ffffff", soft="#f1f4f9",
                  line="#94a3b8", rule="#e6e9ee", chip="#475569",
                  warn="#9a3412", warn_soft="#fff4ed", group="#f7f9fb"),
    "dark":  dict(card="#161b22", border="#30363d", title="#e6edf3", sub="#9aa4b0",
                  accent="#4c7ef3", on_accent="#ffffff", soft="#1b2230",
                  line="#6b7684", rule="#232a33", chip="#aeb7c2",
                  warn="#ffa657", warn_soft="#2a1d14", group="#11151b"),
}


# Stroked glyphs on a 24x24 grid, drawn rather than typed.
ICONS = {
    "clock":    "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18z M12 7v5l3.5 2",
    "package":  "M12 3 20 7.5v9L12 21l-8-4.5v-9z M4 7.5 12 12l8-4.5 M12 12v9",
    "key":      "M8 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8z M12 12h9 M18 12v3.5 M21 12v2.5",
    "terminal": "M3 5h18v14H3z M7 10l3 2.5L7 15 M12.5 15h4.5",
    "bucket":   "M4 6h16l-2 14H6z M4 6c0-1.5 3.6-2.5 8-2.5s8 1 8 2.5",
    "server":   "M4 4h16v7H4z M4 13h16v7H4z M8 7.5h.01 M8 16.5h.01 M12 7.5h4 M12 16.5h4",
    "laptop":   "M5 5h14v10H5z M3 15h18l1.5 4h-21z",
    "tag":      "M3 3h8l10 10-8 8L3 11z M7.5 7.5h.01",
    "shield":   "M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M8.5 12l2.5 2.5 4.5-4.5",
    "users":    "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 1 0 0 7z M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6 M16 4.5a3.5 3.5 0 0 1 0 6.5 M18 14c2.2.6 3.5 2.8 3.5 6",
    "chat":     "M4 5h16v11H9l-5 4z M8 9.5h8 M8 12.5h5",
    "chip":     "M8 8h8v8H8z M5 5h14v14H5z M10 2v3 M14 2v3 M10 19v3 M14 19v3 M2 10h3 M2 14h3 M19 10h3 M19 14h3",
    "bell":     "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z M10 20.5a2 2 0 0 0 4 0",
    "mail":     "M3 6h18v12H3z M3 6l9 7 9-7",
    "user_plus": "M10 11a4 4 0 1 0 0-8 4 4 0 1 0 0 8z M3 21c0-4 3-7 7-7s7 3 7 7 M19 8v6 M16 11h6",
    "user":     "M12 11a4 4 0 1 0 0-8 4 4 0 1 0 0 8z M5 21c0-4 3-7 7-7s7 3 7 7",
    "user_x":   "M10 11a4 4 0 1 0 0-8 4 4 0 1 0 0 8z M3 21c0-4 3-7 7-7s7 3 7 7 M16.5 8.5l5 5 M21.5 8.5l-5 5",
    "user_check": "M10 11a4 4 0 1 0 0-8 4 4 0 1 0 0 8z M3 21c0-4 3-7 7-7s7 3 7 7 M16 11l2 2 4-4",
    "archive":  "M3 4h18v4H3z M5 8v12h14V8 M10 12h4",
    "refresh":  "M20 12a8 8 0 1 1-2.3-5.7 M20 4v4.5h-4.5",
    "alert":    "M12 3 22 20H2z M12 10v4 M12 17h.01",
}


class Canvas:
    """Parts plus a size. No layout engine, on purpose."""

    def __init__(self, w: int, h: int, scheme: str, label: str) -> None:
        self.w, self.h, self.c, self.label = w, h, SCHEMES[scheme], label
        self.parts: list[str] = []

    def add(self, *svg: str) -> "Canvas":
        self.parts.extend(svg)
        return self

    # ── primitives ────────────────────────────────────────────────────────
    def icon(self, name, x, y, colour, size=21):
        s = size / 24
        return (f'<g transform="translate({x:.1f},{y:.1f}) scale({s:.4f})" fill="none" '
                f'stroke="{colour}" stroke-width="1.7" stroke-linecap="round" '
                f'stroke-linejoin="round"><path d="{ICONS[name]}"/></g>')

    def text(self, x, y, s, *, size=13, colour=None, font=None, weight=None,
             anchor="start", opacity=None):
        c = colour or self.c["sub"]
        extra = (f' font-weight="{weight}"' if weight else "") + \
                (f' opacity="{opacity}"' if opacity else "")
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{font or SANS}" '
                f'font-size="{size}" fill="{c}" text-anchor="{anchor}"{extra}>'
                f'{escape(s)}</text>')

    def box(self, x, y, w, h, title, subs=(), *, icon=None, tone="plain", rx=10):
        c = self.c
        fill, edge, tt = c["card"], c["border"], c["title"]
        st, op = c["sub"], ""
        if tone == "accent":
            fill = edge = c["accent"]; tt = st = c["on_accent"]; op = "0.85"
        elif tone == "soft":
            fill = c["soft"]
        elif tone == "warn":
            fill, edge, tt, st = c["warn_soft"], c["warn"], c["warn"], c["warn"]
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
               f'stroke="{edge}" stroke-width="1"/>']
        tx = x + 16
        ty = y + (28 if subs else h / 2 + 5)
        if icon:
            out.append(self.icon(icon, x + 16, y + (13 if subs else h / 2 - 10), tt))
            tx = x + 47
        out.append(self.text(tx, ty, title, size=15 if subs else 14,
                             colour=tt, weight="600"))
        for i, s in enumerate(subs):
            out.append(self.text(x + 16, y + 52 + i * 18, s, size=12.5,
                                 colour=st, opacity=op or None))
        return "".join(out)

    def diamond(self, cx, cy, w, h, lines):
        c = self.c
        pts = f"{cx},{cy - h/2} {cx + w/2},{cy} {cx},{cy + h/2} {cx - w/2},{cy}"
        out = [f'<polygon points="{pts}" fill="{c["soft"]}" stroke="{c["border"]}" '
               f'stroke-width="1"/>']
        n = len(lines)
        for i, s in enumerate(lines):
            out.append(self.text(cx, cy - (n - 1) * 7 + i * 14 + 4, s, size=12,
                                 colour=c["title"], anchor="middle"))
        return "".join(out)

    def pill(self, cx, cy, text, *, tone="plain", pad=16, size=13):
        c = self.c
        w = len(text) * size * 0.58 + pad * 2
        h = 32
        fill, edge, col = c["card"], c["border"], c["title"]
        if tone == "accent":
            fill = edge = c["accent"]; col = c["on_accent"]
        elif tone == "soft":
            fill = c["soft"]
        return (f'<rect x="{cx - w/2:.1f}" y="{cy - h/2}" width="{w:.1f}" height="{h}" '
                f'rx="{h/2}" fill="{fill}" stroke="{edge}" stroke-width="1"/>'
                + self.text(cx, cy + 4.5, text, size=size, colour=col, anchor="middle",
                            weight="500")), w

    def edge(self, pts, *, label=None, dash=False, both=False, label_at=0.5,
             label_dy=-9, label_anchor="middle", mono=True):
        c = self.c
        d = ' stroke-dasharray="5 4"' if dash else ""
        path = " ".join(f"{x},{y}" for x, y in pts)
        out = [f'<polyline points="{path}" fill="none" stroke="{c["line"]}" '
               f'stroke-width="1.5"{d} marker-end="url(#a)"'
               + (' marker-start="url(#a)"' if both else "") + "/>"]
        if label:
            (x1, y1), (x2, y2) = pts[0], pts[-1]
            lx = x1 + (x2 - x1) * label_at
            ly = y1 + (y2 - y1) * label_at
            out.append(self.text(lx, ly + label_dy, label, size=11.5,
                                 colour=c["chip"], font=MONO if mono else SANS,
                                 anchor=label_anchor))
        return "".join(out)

    def group(self, x, y, w, h, title):
        c = self.c
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
                f'fill="{c["group"]}" stroke="{c["border"]}" stroke-width="1" '
                f'stroke-dasharray="6 5"/>'
                + self.text(x + 18, y + 24, title, size=12, colour=c["sub"],
                            weight="600"))

    def footer(self, note):
        return (f'<line x1="24" y1="{self.h - 52}" x2="{self.w - 24}" y2="{self.h - 52}" '
                f'stroke="{self.c["rule"]}" stroke-width="1"/>'
                + self.text(24, self.h - 26, note, size=12.5, colour=self.c["sub"]))

    def render(self) -> str:
        c = self.c
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" aria-label="{escape(self.label)}">'
            f'<defs>'
            f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
            f'markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M0 0 10 5 0 10z" fill="{c["line"]}"/></marker>'
            f'</defs>' + "".join(self.parts) + "</svg>"
        )


# ── the diagrams ──────────────────────────────────────────────────────────

def architecture(scheme):
    """What runs where. One task definition, fired five ways."""
    k = Canvas(1180, 678, scheme,
               "Five EventBridge rules run one Fargate task, which pulls its image from ECR, "
               "reads secrets and the run lock from SSM, logs to CloudWatch and caches AI "
               "decisions in S3. The task reads and writes Jamf Pro, Snipe-IT and Azure AD, "
               "only reads HiBob, reports to Slack and asks the Anthropic API about ambiguous "
               "matches. Failed or unlaunched runs alert through SNS.")
    c = k.c
    ax, aw, ah, gap = 56, 220, 64, 12
    tx, tw, ty, th = 356, 300, 72, 368
    side = [
        ("EventBridge", "5 cron rules run the task", "clock", "in"),
        ("ECR", "image :latest and :base", "package", "in"),
        ("SSM Parameter Store", "secrets and the run lock", "key", "both"),
        ("CloudWatch Logs", "container output", "terminal", "out"),
        ("S3 AI cache", "AI resolver decisions", "bucket", "both"),
    ]
    k.add(k.group(24, 36, 664, 572, "AWS, EU-WEST-1"))
    for i, (title, sub, icon, way) in enumerate(side):
        y = ty + i * (ah + gap)
        mid = y + ah / 2
        k.add(k.box(ax, y, aw, ah, title, [sub], icon=icon))
        if way == "out":
            k.add(k.edge([(tx - 8, mid), (ax + aw + 8, mid)]))
        else:
            k.add(k.edge([(ax + aw + 8, mid), (tx - 8, mid)], both=way == "both"))

    # The task: the one thing that runs, with the module set it can run.
    k.add(k.box(tx, ty, tw, th, "ECS Fargate task",
                ["linux/amd64, read-only root fs", "RUN_MODE picks the module set",
                 "every job holds the RunMutex"], icon="server", tone="accent"))
    y = ty + 122
    for head, rows in (("LIFECYCLE", ["azure-starters, user-enrichment,", "rehire-detection, leavers"]),
                       ("SYNC", ["user-match, correction,", "snipe-to-jamf, model-sync,",
                                 "peripherals-sync"]),
                       ("MAINTENANCE", ["cleanup, health-check, ai-audit,",
                                        "reconciliation, monthly-digest,",
                                        "pending-reconciliation,", "jamf-location-cleanup"])):
        k.add(k.text(tx + 16, y, head, size=11, colour=c["on_accent"], weight="600",
                     opacity="0.85"))
        for j, row in enumerate(rows):
            k.add(k.text(tx + 16, y + 20 + j * 18, row, size=11.5, colour=c["on_accent"],
                         font=MONO))
        y += 20 + len(rows) * 18 + 10

    # Systems of record. HiBob is the only one the task never writes to.
    ex, ew, eh = 872, 284, 48
    step = (th - eh) / 5
    systems = [
        ("Jamf Pro", "laptop", "devices, local accounts", False),
        ("Snipe-IT", "tag", "users, assets, accessories", False),
        ("Azure AD / Entra ID", "shield", "groups, account status", False),
        ("HiBob, read-only", "users", "employees, equipment", True),
        ("Slack", "chat", "summaries, human queue", False),
        ("Anthropic API", "chip", "ambiguous matches, audit", False),
    ]
    for i, (title, icon, label, ro) in enumerate(systems):
        y = ty + i * step
        mid = y + eh / 2
        k.add(k.box(ex, y, ew, eh, title, icon=icon),
              k.edge([(tx + tw + 8, mid), (ex - 8, mid)], label=label, dash=ro))

    # Failure path: a task that stops badly, or a rule that could not start one.
    fy = ty + th + 48
    k.add(k.box(tx, fy, tw, 88, "Failure alerts",
                ["task stopped non-zero, or a", "schedule that failed to launch"], icon="bell"),
          k.edge([(tx + tw / 2, ty + th + 8), (tx + tw / 2, fy - 8)], dash=True),
          k.box(ax, fy, aw, 88, "SNS alerts topic", ["plus an optional", "email subscription"],
                icon="mail"),
          k.edge([(tx - 8, fy + 44), (ax + aw + 8, fy + 44)]),
          k.footer("Five schedules, one task definition: the RUN_MODE each rule passes "
                   "decides which modules run. HiBob is only ever read."))
    return k.render()


def lifecycle(scheme):
    """A person's path through the register, including the way back."""
    k = Canvas(1180, 440, scheme,
               "A new hire becomes an active user. When they leave, their name is tagged "
               "[Disabled] and their assets go to Pending. Rehire detection restores them "
               "only when all four signals agree; anything less is reported to Slack for a "
               "human, and anyone who stays gone is kept for audit, never deleted.")
    c = k.c
    cols = [24, 300, 616, 956]
    ws = [200, 240, 264, 200]
    top, th = 64, 84
    mid = top + th / 2
    k.add(
        k.box(cols[0], top, ws[0], th, "New hire", ["azure-starters creates", "the Snipe-IT user"],
              icon="user_plus"),
        k.box(cols[1], top, ws[1], th, "Active", ["assets checked out", "by user-match"], icon="user"),
        k.box(cols[2], top, ws[2], th, "[Disabled]", ["leavers tags the name,", "assets set to Pending"],
              icon="user_x"),
        k.box(cols[3], top, ws[3], th, "Kept for audit", ["never deleted"], icon="archive",
              tone="soft"),
        k.edge([(cols[0] + ws[0] + 8, mid), (cols[1] - 8, mid)]),
        k.edge([(cols[1] + ws[1] + 8, mid), (cols[2] - 8, mid)], label="leaves"),
        k.edge([(cols[2] + ws[2] + 8, mid), (cols[3] - 8, mid)], label="stays gone"),
    )
    # The check: four independent signals, all required.
    ry, rh = 236, 124
    rmid = ry + rh / 2
    k.add(
        k.box(cols[2], ry, ws[2], rh, "Rehire detection",
              ["Azure AD account enabled", "not in leavers or disabled group",
               "no leave date in the past", "HiBob lists them as active"],
              icon="refresh", tone="accent"),
        k.edge([(cols[2] + ws[2] / 2, top + th + 8), (cols[2] + ws[2] / 2, ry - 8)]),
        k.text(cols[2] + ws[2] / 2 + 12, (top + th + ry) / 2 + 4, "every [Disabled] user",
               size=11.5, font=MONO, colour=c["chip"]),
        k.box(cols[1], rmid - 50, ws[1], 100, "Restored",
              ["[Disabled] prefix stripped,", "Pending assets to Deployed"], icon="user_check"),
        k.edge([(cols[2] - 8, rmid), (cols[1] + ws[1] + 8, rmid)], label="all four"),
        k.edge([(cols[1] + ws[1] / 2, rmid - 50 - 8), (cols[1] + ws[1] / 2, top + th + 8)]),
        k.text(cols[1] + ws[1] / 2 + 12, (top + th + rmid - 50) / 2 + 4, "back to active",
               size=11.5, font=MONO, colour=c["chip"]),
        k.box(cols[3], rmid - 50, ws[3], 100, "Ambiguous",
              ["reported to Slack,", "nothing is changed"], icon="alert", tone="warn"),
        k.edge([(cols[2] + ws[2] + 8, rmid), (cols[3] - 8, rmid)], label="any fails"),
        k.footer("The suite never guesses about people: a restore needs every signal, and "
                 "anything less waits for a human."),
    )
    return k.render()


DIAGRAMS = {
    "architecture": architecture,
    "lifecycle": lifecycle,
}


def main() -> None:
    out = ROOT / "docs" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS.items():
        for scheme in SCHEMES:
            path = out / f"{name}-{scheme}.svg"
            path.write_text(fn(scheme), encoding="utf-8")
    print(f"{len(DIAGRAMS)} diagrams x {len(SCHEMES)} schemes -> docs/assets/")


if __name__ == "__main__":
    main()
