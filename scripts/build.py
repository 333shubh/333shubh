#!/usr/bin/env python3
"""Rebuilds README.md and assets/*.svg from live GitHub data.

    GITHUB_TOKEN=... python scripts/build.py          # live
    python scripts/build.py --data response.json      # from a saved API response

Standard library only. Text is drawn as vector outlines of Instrument Serif
and Inter (scripts/glyphs.json), so the SVGs need no font at view time. Every
image is written in a light and a dark variant; the README picks one with
<picture>, which follows the viewer's GitHub theme.
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
GLYPHS = json.loads((ROOT / "scripts" / "glyphs.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

W = 864
THEMES = {  # greys only
    "light": {"ink": "#111111", "muted": "#767676", "line": "#e6e6e6", "faint": "#d2d2d2", "ring": "#cfcfcf",
              "base": "#f4f4f4", "glow": ["#dcdcdc", "#ffffff", "#e4e4e4"]},
    "dark": {"ink": "#f2f2f2", "muted": "#8c8c8c", "line": "#2a2a2a", "faint": "#3d3d3d", "ring": "#383838",
             "base": "#131313", "glow": ["#303030", "#050505", "#262626"]},
}

# Dependency name -> stack item, in display priority order.
TECH = [
    ("Next.js", {"next"}),
    ("React", {"react"}),
    ("React Native", {"react-native"}),
    ("Expo", {"expo"}),
    ("Three.js", {"three"}),
    ("Tailwind", {"tailwindcss"}),
    ("FastAPI", {"fastapi"}),
    ("Flask", {"flask"}),
    ("Django", {"django"}),
    ("Supabase", {"@supabase/supabase-js", "supabase"}),
    ("Express", {"express"}),
    ("Vue", {"vue"}),
    ("Svelte", {"svelte"}),
    ("Vite", {"vite"}),
    ("PyTorch", {"torch"}),
    ("TensorFlow", {"tensorflow"}),
    ("Pandas", {"pandas"}),
    ("NumPy", {"numpy"}),
    ("Prisma", {"prisma", "@prisma/client"}),
    ("SQLAlchemy", {"sqlalchemy"}),
    ("MapLibre", {"maplibre-gl"}),
    ("Vitest", {"vitest"}),
    ("Pytest", {"pytest"}),
]
LANG_LABEL = {"Jupyter Notebook": "Jupyter", "PLpgSQL": "Postgres SQL"}
PROVIDERS = {"LINKEDIN": "LinkedIn", "TWITTER": "X / Twitter", "YOUTUBE": "YouTube",
             "INSTAGRAM": "Instagram", "MASTODON": "Mastodon", "BLUESKY": "Bluesky",
             "FACEBOOK": "Facebook", "TWITCH": "Twitch", "REDDIT": "Reddit", "NPM": "npm"}

QUERY = """
query($login: String!) {
  user(login: $login) {
    name login bio websiteUrl
    socialAccounts(first: 6) { nodes { provider url } }
    contributionsCollection { contributionCalendar { totalContributions
      weeks { contributionDays { contributionCount } } } }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) { totalCount nodes { ...R } }
    repositoriesContributedTo(first: 10, privacy: PUBLIC, contributionTypes: [COMMIT, PULL_REQUEST],
                              orderBy: {field: STARGAZERS, direction: DESC}) { nodes { ...R } }
  }
}
fragment R on Repository {
  name nameWithOwner isArchived isFork
  owner { login }
  languages(first: 8, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } }
  pkg: object(expression: "HEAD:package.json") { ... on Blob { text } }
  pkgFrontend: object(expression: "HEAD:frontend/package.json") { ... on Blob { text } }
  req: object(expression: "HEAD:requirements.txt") { ... on Blob { text } }
  reqBackend: object(expression: "HEAD:backend/requirements.txt") { ... on Blob { text } }
  pyproject: object(expression: "HEAD:pyproject.toml") { ... on Blob { text } }
}
"""


# ---------------------------------------------------------------- data

def fetch(login):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("Set GITHUB_TOKEN, or pass --data <file> with a saved API response.")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"Bearer {token}", "User-Agent": "profile-readme"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        body = json.load(res)
    for err in body.get("errors", []):
        print("GraphQL warning:", err.get("message"), file=sys.stderr)
    if not (body.get("data") or {}).get("user"):
        sys.exit("GitHub returned no user data.")
    return body["data"]["user"]


def blob(repo, key):
    return (repo.get(key) or {}).get("text") or ""


def dependencies(repo):
    names = set()
    for key in ("pkg", "pkgFrontend"):
        try:
            pkg = json.loads(blob(repo, key) or "{}")
        except ValueError:
            continue
        for field in ("dependencies", "devDependencies"):
            names.update(pkg.get(field) or {})
    for key in ("req", "reqBackend"):
        names.update(m.group(0).lower() for line in blob(repo, key).splitlines()
                     if (m := re.match(r"[A-Za-z0-9_.-]+", line.strip())))
    names.update(n.lower() for n in re.findall(r"[\"']([A-Za-z0-9_.-]+)", blob(repo, "pyproject")))
    return names


def techs(repo):
    deps = dependencies(repo)
    return [t[0] for t in TECH if t[1] & deps]


def summarise(user):
    hidden = set(CONFIG.get("hide", [])) | {user["login"]}
    own = [r for r in user["repositories"]["nodes"]
           if r["name"] not in hidden and not r["isArchived"] and not r["isFork"]]
    contributed = [r for r in (user.get("repositoriesContributedTo") or {}).get("nodes", [])
                   if r and r["owner"]["login"] != user["login"]]

    # Languages by bytes across owned repos, then frameworks by how many repos use them.
    langs = {}
    for r in own:
        for e in r["languages"]["edges"]:
            langs[e["node"]["name"]] = langs.get(e["node"]["name"], 0) + e["size"]
    all_bytes = sum(langs.values()) or 1

    counts = {}
    for r in own + contributed:
        for t in techs(r):
            counts[t] = counts.get(t, 0) + 1
    frameworks = sorted((t[0] for t in TECH if t[0] in counts), key=lambda t: -counts[t])
    share, other = [], 100
    for name, size in sorted(langs.items(), key=lambda kv: -kv[1])[:4]:
        pct = round(100 * size / all_bytes)
        if pct >= 2:
            share.append((LANG_LABEL.get(name, name), pct))
            other -= pct
    if share and other >= 2:
        share.append(("Other", other))

    calendar = user["contributionsCollection"]["contributionCalendar"]
    streak = run = 0
    for day in (day for week in calendar.get("weeks", []) for day in week["contributionDays"]):
        run = run + 1 if day["contributionCount"] else 0
        streak = max(streak, run)

    links = [(PROVIDERS.get(a["provider"], re.sub(r"^https?://(www\.)?|/.*$", "", a["url"])), a["url"])
             for a in (user.get("socialAccounts") or {}).get("nodes", [])]
    if user.get("websiteUrl"):
        url = user["websiteUrl"]
        links.insert(0, ("Website", url if "://" in url else f"https://{url}"))
    links += [tuple(pair) for pair in CONFIG.get("extra_links", [])]

    return {
        "login": user["login"],
        "name": user.get("name") or user["login"],
        "bio": user.get("bio") or "",
        "language_share": share,
        "frameworks": frameworks[:12],
        "streak": streak,
        "collaborations": [r["nameWithOwner"] for r in contributed],
        "links": links,
        "contributions": calendar["totalContributions"],
        # the profile repo itself is not a project
        "repo_count": user["repositories"]["totalCount"] - any(r["name"] == user["login"] for r in user["repositories"]["nodes"]),
    }


# ---------------------------------------------------------------- drawing

class Canvas:
    def __init__(self, h, label, theme, w=W):
        self.w, self.h, self.title, self.t = w, h, label, THEMES[theme]
        self.defs, self.body = {}, []

    def add(self, s):
        self.body.append(s)

    def line(self, y):
        self.add(f'<rect x="0" y="{y}" width="{self.w}" height="1" fill="{self.t["line"]}"/>')

    def text(self, x, y, size, string, fill, face="sans", anchor="start", spacing=0):
        """Draws `string` with its baseline at y and returns its width. `spacing` is tracking in em/1000."""
        width = text_w(string, size, face, spacing)
        x = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
        uses, pen = [], 0
        for ch in string:
            f = face if ch in GLYPHS[face] else "sans"
            ch = ch if ch in GLYPHS[f] else "?"
            advance, d = GLYPHS[f][ch]
            if d:
                gid = f"{f[:2]}{ord(ch):x}"
                self.defs[gid] = f'<path id="{gid}" d="{d}"/>'
                uses.append(f'<use href="#{gid}" x="{pen}"/>')
            pen += advance + spacing
        self.add(f'<g fill="{fill}" transform="translate({x:.1f} {y}) scale({size / 1000:.4f})">{"".join(uses)}</g>')
        return width

    def label(self, x, y, string, anchor="start"):
        return self.text(x, y, 11.5, string.upper(), self.t["muted"], spacing=140, anchor=anchor)

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{html.escape(self.title)}">'
                f'<title>{html.escape(self.title)}</title>'
                f'<defs>{"".join(self.defs.values())}</defs>{"".join(self.body)}</svg>\n')



def text_w(string, size, face="sans", spacing=0):
    sans = GLYPHS["sans"]
    units = sum((GLYPHS[face].get(ch) or sans.get(ch) or sans["?"])[0] + spacing for ch in string)
    return units * size / 1000


def wrap(words, size, width, face="sans", joiner=" "):
    lines, line = [], []
    for word in words:
        if line and text_w(joiner.join(line + [word]), size, face) > width:
            lines.append(line)
            line = []
        line.append(word)
    return lines + [line] if line else lines


def grey(a, b, t):
    """A grey between hex colours a and b."""
    x, y = int(a[1:3], 16), int(b[1:3], 16)
    return "#" + f"{round(x + (y - x) * t):02x}" * 3


def header(d, theme):
    h = 300
    c = Canvas(h, f'{d["name"]}. {d["bio"]}', theme)
    t = c.t
    c.defs["clip"] = f'<clipPath id="clip"><rect width="{W}" height="{h}" rx="28"/></clipPath>'
    c.defs["blur"] = ('<filter id="blur" x="-50%" y="-50%" width="200%" height="200%">'
                      '<feGaussianBlur stdDeviation="70"/></filter>')
    c.add(f'<g clip-path="url(#clip)"><rect width="{W}" height="{h}" fill="{t["base"]}"/><g filter="url(#blur)">')
    # three soft pools of grey, drifting slowly
    pools = [(620, 40, 200), (840, 290, 190), (400, 320, 170)]
    drifts = [(-70, 40), (-50, -60), (80, -30)]
    for colour, (cx, cy, r), (dx, dy), dur in zip(t["glow"], pools, drifts, (26, 32, 38)):
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}"><animateTransform attributeName="transform" '
              f'type="translate" values="0 0;{dx} {dy};0 0" dur="{dur}s" repeatCount="indefinite" '
              'calcMode="spline" keyTimes="0;0.5;1" keySplines=".45 0 .55 1;.45 0 .55 1"/></circle>')
    c.add("</g>")
    # orbits: hairline rings with a point travelling round two of them
    ox, oy = 694, 168
    for i, r in enumerate((52, 96, 140, 184)):
        c.add(f'<circle cx="{ox}" cy="{oy}" r="{r}" fill="none" stroke="{t["ring"]}" stroke-width="1"/>')
        if i % 2 == 0:
            c.add(f'<circle cx="{ox + r}" cy="{oy}" r="{3.5 - i / 2}" fill="{t["ink"]}"><animateTransform '
                  f'attributeName="transform" type="rotate" from="{40 + i * 80} {ox} {oy}" '
                  f'to="{400 + i * 80} {ox} {oy}" dur="{36 + i * 22}s" repeatCount="indefinite"/></circle>')
    c.add(f'<circle cx="{ox}" cy="{oy}" r="2" fill="{t["ink"]}"/></g>')

    x = 56
    status = CONFIG.get("status")
    if status:
        c.add(f'<circle cx="{x + 4}" cy="60" r="4" fill="{t["ink"]}"/>')
        c.text(x + 18, 65, 12, status.upper(), t["muted"], spacing=110)
    size = 104
    while text_w(d["name"], size, "serif") > 420 and size > 44:
        size -= 4
    c.text(x - 4, 168, size, d["name"], t["ink"], face="serif")
    for i, line in enumerate(wrap(d["bio"].split(), 18, 420)[:3]):
        c.text(x, 214 + i * 27, 18, " ".join(line), t["muted"])
    return c.render()


def stack(d, theme):
    lines = wrap(d["frameworks"], 17, W - 8, joiner="  ·  ")
    langs = d["language_share"]
    h = 46 + (70 if langs else 0) + 32 * len(lines) + 12
    c = Canvas(h, "Stack. " + ", ".join(f"{n} {p}%" for n, p in langs) + ". " + ", ".join(d["frameworks"]), theme)
    t = c.t
    c.label(0, 30, "Stack")
    y = 46
    if langs:
        # one bar, split by share of code; darkest is the most used
        x = 0
        for i, (name, pct) in enumerate(langs):
            shade = grey(t["ink"], t["faint"], i / max(1, len(langs) - 1))
            w = max(6, (W - 3 * (len(langs) - 1)) * pct / 100)
            c.add(f'<rect x="{x:.1f}" y="{y + 6}" width="{w:.1f}" height="8" rx="2" fill="{shade}"/>')
            x += w + 3
        x = 0
        for i, (name, pct) in enumerate(langs):
            shade = grey(t["ink"], t["faint"], i / max(1, len(langs) - 1))
            c.add(f'<rect x="{x:.1f}" y="{y + 31}" width="9" height="9" rx="2" fill="{shade}"/>')
            x += 16 + c.text(x + 16, y + 40, 14, name, t["ink"])
            x += 6 + c.text(x + 6, y + 40, 14, f"{pct}%", t["muted"]) + 30
        y += 70
    for i, line in enumerate(lines):
        x = 0
        for j, item in enumerate(line):
            if j:
                x += c.text(x, y + 22 + i * 32, 17, "  ·  ", t["muted"])
            x += c.text(x, y + 22 + i * 32, 17, item, t["ink"])
    return c.render()


def link_w(text):
    return round(text_w(text, 24, "italic") + 34)


def link(text, theme):
    c = Canvas(72, text, theme, link_w(text))
    x = c.text(0, 52, 24, text, c.t["ink"], face="italic")
    c.text(x + 8, 51, 17, "↗", c.t["muted"])
    return c.render()


def heading(title, theme):
    c = Canvas(52, title, theme)
    c.label(0, 30, title)
    return c.render()


def figure(number, what, linked, theme):
    c = Canvas(90, f"{number} {what}", theme, W // 4)
    x = c.text(-2, 44, 52, number, c.t["ink"], face="serif")
    if linked:
        c.text(x + 6, 22, 15, "↗", c.t["muted"])
    c.text(0, 68, 12.5, what, c.t["muted"])
    return c.render()


def figures(d):
    """(number, caption, link) for each summary figure; a link opens the list being counted."""
    profile = f'https://github.com/{d["login"]}'
    out = [(f'{d["contributions"]:,}', "contributions this year", None),
           (str(d["streak"]), "day longest streak", None),
           (str(d["repo_count"]), "public repositories", f"{profile}?tab=repositories")]
    repos = d["collaborations"]
    if len(repos) == 1:
        out.append(("1", "collaboration", f"https://github.com/{repos[0]}"))
    elif repos:
        # GitHub has no page for repos contributed to, so search for exactly these
        query = urllib.parse.quote(" ".join(f"repo:{r}" for r in repos))
        out.append((str(len(repos)), "collaborations", f"https://github.com/search?q={query}&type=repositories"))
    return out


# ---------------------------------------------------------------- output

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build(user):
    d = summarise(user)
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("*.svg"):
        old.unlink()

    def picture(name, draw, alt, width="100%"):
        for theme in THEMES:
            (ASSETS / f"{name}-{theme}.svg").write_text(draw(theme), encoding="utf-8", newline="\n")
        return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">'
                f'<img src="assets/{name}-light.svg" width="{width}" alt="{html.escape(alt, quote=True)}"></picture>')

    out = ["<!-- Generated by scripts/build.py. Change config.json or the script, not this file. -->", "",
           picture("header", lambda th: header(d, th), f'{d["name"]}. {d["bio"]}'), ""]

    if d["language_share"] or d["frameworks"]:
        alt = ", ".join([f"{n} {p}%" for n, p in d["language_share"]] + d["frameworks"])
        out += [picture("stack", lambda th: stack(d, th), "Stack: " + alt), ""]
    row = []
    for number, what, url in figures(d):
        image = picture("figure-" + slug(what), lambda th: figure(number, what, url, th), f"{number} {what}", "25%")
        row.append(f'<a href="{html.escape(url, quote=True)}">{image}</a>' if url else image)
    # one line, no gaps: whitespace between the images would push the fourth onto a new row
    out += [picture("summary", lambda th: heading("Summary", th), "Summary"), "<p>" + "".join(row) + "</p>", ""]
    if d["links"]:
        out.append('<p align="center">')
        for text, url in d["links"]:
            out.append(f'<a href="{html.escape(url, quote=True)}">'
                       f'{picture("link-" + slug(text), lambda th: link(text, th), text, link_w(text))}</a>')
        out += ["</p>", ""]
    (ROOT / "README.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f'Built README for {d["login"]}: {d["contributions"]} contributions, '
          f'languages {d["language_share"]}, frameworks {d["frameworks"]}')


if __name__ == "__main__":
    login = CONFIG.get("login") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if "--data" in sys.argv:
        saved = json.loads(Path(sys.argv[sys.argv.index("--data") + 1]).read_text(encoding="utf-8"))
        build(saved["data"]["user"] if "data" in saved else saved)
    else:
        build(fetch(login))
