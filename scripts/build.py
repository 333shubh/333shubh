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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
GLYPHS = json.loads((ROOT / "scripts" / "glyphs.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

W = 864
THEMES = {
    "light": {"ink": "#2b2a27", "muted": "#85827a", "line": "#e4e0d8", "base": "#f5f2ec",
              "glow": ["#d9e6d6", "#f4e1d2", "#e2ddf1"]},
    "dark": {"ink": "#e9e5dd", "muted": "#8f8c85", "line": "#2b2e33", "base": "#16191d",
             "glow": ["#22362c", "#35283a", "#1f2c3d"]},
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
    contributionsCollection { contributionCalendar { totalContributions } }
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository { ...R } } }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) { totalCount nodes { ...R } }
    repositoriesContributedTo(first: 10, privacy: PUBLIC, contributionTypes: [COMMIT, PULL_REQUEST],
                              orderBy: {field: STARGAZERS, direction: DESC}) { nodes { ...R } }
  }
}
fragment R on Repository {
  name nameWithOwner url description stargazerCount pushedAt isArchived isFork
  owner { login }
  primaryLanguage { name color }
  languages(first: 8, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name color } } }
  pkg: object(expression: "HEAD:package.json") { ... on Blob { text } }
  pkgFrontend: object(expression: "HEAD:frontend/package.json") { ... on Blob { text } }
  req: object(expression: "HEAD:requirements.txt") { ... on Blob { text } }
  reqBackend: object(expression: "HEAD:backend/requirements.txt") { ... on Blob { text } }
  pyproject: object(expression: "HEAD:pyproject.toml") { ... on Blob { text } }
  readme: object(expression: "HEAD:README.md") { ... on Blob { text } }
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


def strip_markdown(s):
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s)).strip()


def describe(repo, limit):
    """The repo description, else the opening sentences of its README."""
    text = CONFIG.get("descriptions", {}).get(repo["name"]) or repo.get("description")
    if not text:
        for para in re.split(r"\n\s*\n", blob(repo, "readme")):
            para = strip_markdown(para)
            if para and not para.startswith(("#", "|", "-", ">", "```")):
                text = para
                break
    text = strip_markdown(text or "")
    if len(text) <= limit:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[: limit - 1].rstrip() + "…"


def summarise(user):
    hidden = set(CONFIG.get("hide", [])) | {user["login"]}
    own = [r for r in user["repositories"]["nodes"]
           if r["name"] not in hidden and not r["isArchived"] and not r["isFork"]]
    contributed = [r for r in (user.get("repositoriesContributedTo") or {}).get("nodes", [])
                   if r and r["owner"]["login"] != user["login"] and r["name"] not in hidden]

    worlds, seen = [], set()
    pinned = [r for r in (user.get("pinnedItems") or {}).get("nodes", []) if r and r["name"] not in hidden]
    for r in pinned + own + contributed[:2]:
        if r["nameWithOwner"] not in seen and len(worlds) < CONFIG.get("max_worlds", 6):
            seen.add(r["nameWithOwner"])
            worlds.append(r)

    # Languages by bytes across owned repos, then frameworks by how many repos use them.
    langs = {}
    for r in own:
        total = sum(e["size"] for e in r["languages"]["edges"]) or 1
        for e in r["languages"]["edges"]:
            item = langs.setdefault(e["node"]["name"], {"size": 0, "repos": 0, "color": e["node"]["color"] or "#8b8b8b"})
            item["size"] += e["size"]
            item["repos"] += e["size"] / total >= 0.05
    all_bytes = sum(v["size"] for v in langs.values()) or 1
    languages = [LANG_LABEL.get(k, k) for k, v in sorted(langs.items(), key=lambda kv: -kv[1]["size"])
                 if v["size"] / all_bytes >= 0.02 and v["repos"]][:6]

    counts = {}
    for r in own + contributed:
        for t in techs(r):
            counts[t] = counts.get(t, 0) + 1
    frameworks = sorted((t[0] for t in TECH if t[0] in counts), key=lambda t: -counts[t])
    stack = (languages + frameworks)[:12]

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
        "worlds": worlds,
        "stack": stack,
        "links": links,
        "contributions": user["contributionsCollection"]["contributionCalendar"]["totalContributions"],
        # the profile repo itself is not a project
        "repo_count": user["repositories"]["totalCount"] - any(r["name"] == user["login"] for r in user["repositories"]["nodes"]),
        "stars": sum(r["stargazerCount"] for r in own),
        "top_language": languages[0] if languages else "",
    }


# ---------------------------------------------------------------- drawing

class Canvas:
    def __init__(self, h, label, theme, w=W):
        self.w, self.h, self.label, self.t = w, h, label, THEMES[theme]
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

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{html.escape(self.label)}">'
                f'<title>{html.escape(self.label)}</title>'
                f'<defs>{"".join(self.defs.values())}</defs>{"".join(self.body)}</svg>\n')


def text_w(string, size, face="sans", spacing=0):
    sans = GLYPHS["sans"]
    units = sum((GLYPHS[face].get(ch) or sans.get(ch) or sans["?"])[0] + spacing for ch in string)
    return units * size / 1000


def fit(string, size, width, face="sans"):
    if text_w(string, size, face) <= width:
        return string
    while string and text_w(string + "…", size, face) > width:
        string = string[:-1]
    return string.rstrip(" ,.;:") + "…"


def wrap(words, size, width, face="sans", joiner=" "):
    lines, line = [], []
    for word in words:
        if line and text_w(joiner.join(line + [word]), size, face) > width:
            lines.append(line)
            line = []
        line.append(word)
    return lines + [line] if line else lines


def header(d, theme):
    h = 300
    c = Canvas(h, f'{d["name"]}. {d["bio"]}', theme)
    t = c.t
    c.defs["clip"] = f'<clipPath id="clip"><rect width="{W}" height="{h}" rx="28"/></clipPath>'
    c.defs["blur"] = ('<filter id="blur" x="-50%" y="-50%" width="200%" height="200%">'
                      '<feGaussianBlur stdDeviation="70"/></filter>')
    c.add(f'<g clip-path="url(#clip)"><rect width="{W}" height="{h}" fill="{t["base"]}"/><g filter="url(#blur)">')
    # three soft pools of colour, drifting slowly
    pools = [(620, 60, 200), (800, 280, 180), (420, 300, 170)]
    drifts = [(-70, 40), (-50, -60), (80, -30)]
    for colour, (cx, cy, r), (dx, dy), dur in zip(t["glow"], pools, drifts, (26, 32, 38)):
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}"><animateTransform attributeName="transform" '
              f'type="translate" values="0 0;{dx} {dy};0 0" dur="{dur}s" repeatCount="indefinite" '
              'calcMode="spline" keyTimes="0;0.5;1" keySplines=".45 0 .55 1;.45 0 .55 1"/></circle>')
    c.add("</g></g>")

    x = 56
    status = CONFIG.get("status")
    if status:
        c.add(f'<circle cx="{x + 4}" cy="60" r="4" fill="#7fa58a"/>')
        c.text(x + 18, 65, 12.5, status.upper(), t["muted"], spacing=110)
    size = 104
    while text_w(d["name"], size, "serif") > W - 2 * x and size > 40:
        size -= 4
    c.text(x - 4, 168, size, d["name"], t["ink"], face="serif")
    for i, line in enumerate(wrap(d["bio"].split(), 18, 500)[:3]):
        c.text(x, 214 + i * 27, 18, " ".join(line), t["muted"])
    return c.render()


def label(c, y, string):
    c.text(0, y, 11.5, string.upper(), c.t["muted"], spacing=140)


def stack(d, theme):
    lines = wrap(d["stack"], 17, W - 8, joiner="  ·  ")
    c = Canvas(64 + 32 * len(lines), "Stack: " + ", ".join(d["stack"]), theme)
    label(c, 30, "Stack")
    for i, line in enumerate(lines):
        x = 0
        for j, item in enumerate(line):
            if j:
                x += c.text(x, 66 + i * 32, 17, "  ·  ", c.t["muted"])
            x += c.text(x, 66 + i * 32, 17, item, c.t["ink"])
    return c.render()


def heading(title, theme):
    c = Canvas(52, title, theme)
    label(c, 30, title)
    return c.render()


def project(repo, d, theme):
    own = repo["owner"]["login"] == d["login"]
    name = repo["name"].strip("-_")
    c = Canvas(104, f"{name}: {describe(repo, 116)}", theme)
    t = c.t
    c.line(0)
    x = c.text(0, 46, 31, name, t["ink"], face="serif")
    if not own:
        c.text(x + 12, 46, 19, f'with {repo["owner"]["login"]}', t["muted"], face="italic")

    lang = repo.get("primaryLanguage") or {}
    pushed = datetime.datetime.fromisoformat(repo["pushedAt"].replace("Z", "+00:00"))
    c.text(W, 40, 13.5, f"{pushed:%b %Y}", t["muted"], anchor="end")
    tags = [lang["name"]] * bool(lang.get("name")) + techs(repo)[:2]
    right = c.text(W, 74, 13.5, "  ·  ".join(tags), t["muted"], anchor="end")
    if lang.get("name"):
        dot = lang.get("color") or t["muted"]
        c.add(f'<circle cx="{W - right - 12:.1f}" cy="69.5" r="3.5" fill="{dot}" opacity=".85"/>')
    c.text(0, 74, 15.5, fit(describe(repo, 116), 15.5, W - right - 60), t["muted"])
    return c.render()


def footer(d, theme):
    parts = [f'{d["contributions"]:,} contributions in the last year', f'{d["repo_count"]} repositories']
    if d["top_language"]:
        parts.append(f'mostly {d["top_language"]}')
    c = Canvas(64, ", ".join(parts), theme)
    c.line(0)
    c.text(W / 2, 42, 13.5, "   ·   ".join(parts), c.t["muted"], anchor="middle")
    return c.render()


def link_w(text):
    return round(text_w(text, 24, "italic") + 34)


def link(text, theme):
    c = Canvas(44, text, theme, link_w(text))
    x = c.text(0, 30, 24, text, c.t["ink"], face="italic")
    c.text(x + 8, 29, 17, "↗", c.t["muted"])
    return c.render()


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
    if d["stack"]:
        out += [picture("stack", lambda th: stack(d, th), "Stack: " + ", ".join(d["stack"])), ""]
    if d["worlds"]:
        out.append(picture("work", lambda th: heading("Selected work", th), "Selected work"))
        for repo in d["worlds"]:
            own = repo["owner"]["login"] == d["login"]
            name = f'project-{slug(repo["name"] if own else repo["nameWithOwner"])}'
            alt = f'{repo["name"].strip("-_")}: {describe(repo, 116)}'
            out.append(f'<a href="{html.escape(repo["url"], quote=True)}">'
                       f'{picture(name, lambda th: project(repo, d, th), alt)}</a>')
        out.append("")
    out += [picture("footer", lambda th: footer(d, th),
                    f'{d["contributions"]} contributions in the last year, {d["repo_count"]} repositories'), ""]
    if d["links"]:
        out.append('<p align="center">')
        for text, url in d["links"]:
            out.append(f'<a href="{html.escape(url, quote=True)}">'
                       f'{picture("link-" + slug(text), lambda th: link(text, th), text, link_w(text))}</a>')
        out += ["</p>", ""]
    (ROOT / "README.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f'Built README for {d["login"]}: {len(d["worlds"])} projects, stack: {", ".join(d["stack"])}')


if __name__ == "__main__":
    login = CONFIG.get("login") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if "--data" in sys.argv:
        saved = json.loads(Path(sys.argv[sys.argv.index("--data") + 1]).read_text(encoding="utf-8"))
        build(saved["data"]["user"] if "data" in saved else saved)
    else:
        build(fetch(login))
