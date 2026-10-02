#!/usr/bin/env python3
"""Rebuilds README.md and assets/*.svg from live GitHub data.

    GITHUB_TOKEN=... python scripts/build.py          # live
    python scripts/build.py --data response.json      # from a saved API response

Standard library only. Text is drawn as vector outlines of the Monocraft
pixel font (scripts/glyphs.json), so the SVGs need no font at view time.
"""
import datetime
import hashlib
import html
import json
import os
import random
import re
import sys
import textwrap
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
GLYPHS = json.loads((ROOT / "scripts" / "glyphs.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

W = 864        # every image is this wide; 18 blocks of 48px
BLOCK = 48     # one 16x16 texture at 3x
PAD = 24

# Dependency name -> stack item, in display priority order.
TECH = [
    ("Next.js", "NX", "#2b2b2b", {"next"}),
    ("React", "RE", "#61dafb", {"react"}),
    ("React Native", "RN", "#4cc2e4", {"react-native"}),
    ("Expo", "EX", "#4630eb", {"expo"}),
    ("Three.js", "3JS", "#049ef4", {"three"}),
    ("Tailwind", "TW", "#38bdf8", {"tailwindcss"}),
    ("FastAPI", "FA", "#009688", {"fastapi"}),
    ("Flask", "FL", "#3babc3", {"flask"}),
    ("Django", "DJ", "#0c4b33", {"django"}),
    ("Supabase", "SB", "#3ecf8e", {"@supabase/supabase-js", "supabase"}),
    ("Express", "EXP", "#6b6b6b", {"express"}),
    ("Vue", "VUE", "#41b883", {"vue"}),
    ("Svelte", "SV", "#ff3e00", {"svelte"}),
    ("Vite", "VI", "#9575ff", {"vite"}),
    ("PyTorch", "PT", "#ee4c2c", {"torch"}),
    ("TensorFlow", "TF", "#ff6f00", {"tensorflow"}),
    ("Pandas", "PD", "#150458", {"pandas"}),
    ("NumPy", "NP", "#4d77cf", {"numpy"}),
    ("Prisma", "PR", "#2d3748", {"prisma", "@prisma/client"}),
    ("SQLAlchemy", "SQL", "#d71f00", {"sqlalchemy"}),
    ("MapLibre", "MAP", "#396cb2", {"maplibre-gl"}),
    ("Vitest", "VT", "#6e9f18", {"vitest"}),
    ("Pytest", "TST", "#0a9edc", {"pytest"}),
]
LANG_ABBR = {
    "TypeScript": "TS", "JavaScript": "JS", "Python": "PY", "HTML": "HTM", "CSS": "CSS",
    "Shell": "SH", "Go": "GO", "Rust": "RS", "Java": "JV", "Kotlin": "KT", "Swift": "SW",
    "C++": "C++", "C": "C", "C#": "C#", "Dart": "DT", "Ruby": "RB", "PHP": "PHP",
    "Jupyter Notebook": "NB", "SCSS": "SC", "PLpgSQL": "SQL", "GLSL": "GL",
}
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
    return [t for t in TECH if t[3] & deps]


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

    # Languages by bytes across owned repos; the stack count is how many repos use each.
    langs = {}
    for r in own:
        total = sum(e["size"] for e in r["languages"]["edges"]) or 1
        for e in r["languages"]["edges"]:
            item = langs.setdefault(e["node"]["name"], {"size": 0, "repos": 0, "color": e["node"]["color"] or "#8b8b8b"})
            item["size"] += e["size"]
            item["repos"] += e["size"] / total >= 0.05
    all_bytes = sum(v["size"] for v in langs.values()) or 1
    languages = [(LANG_LABEL.get(k, k), LANG_ABBR.get(k, k[:2].upper()), v["color"], v["repos"])
                 for k, v in sorted(langs.items(), key=lambda kv: -kv[1]["size"])
                 if v["size"] / all_bytes >= 0.02 and v["repos"]][:6]

    counts = {}
    for r in own + contributed:
        for t in techs(r):
            counts[t[0]] = counts.get(t[0], 0) + 1
    frameworks = sorted((t for t in TECH if t[0] in counts), key=lambda t: -counts[t[0]])
    stack = (languages + [(t[0], t[1], t[2], counts[t[0]]) for t in frameworks])[:12]
    if len(stack) > 6:  # keep every inventory row full
        stack = stack[: len(stack) // 6 * 6]

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
        "top_language": languages[0][0] if languages else "",
    }


# ---------------------------------------------------------------- drawing

def rgb(c):
    c = c.lstrip("#")
    return [int(c[i:i + 2], 16) for i in (0, 2, 4)]


def mix(c, other, t):
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(rgb(c), rgb(other)))


def lighter(c, t):
    return mix(c, "#ffffff", t)


def darker(c, t):
    return mix(c, "#000000", t)


def seeded(key):
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))


def noise(key, palette, weights):
    rng = seeded(key)
    return [rng.choices(palette, weights, k=16) for _ in range(16)]


def tex_dirt(key):
    return noise(key, ["#866043", "#79553a", "#9b7653", "#593d29", "#6c4a32"], [5, 3, 2, 1, 2])


def tex_grass(key):
    grid, rng = tex_dirt(key), seeded(key + "g")
    greens, weights = ["#5d9c3a", "#6aae42", "#4f8a30", "#79bd4f"], [4, 3, 2, 1]
    for x in range(16):
        for y in range(3 + rng.choice([0, 0, 1, 1, 2])):
            grid[y][x] = rng.choices(greens, weights)[0]
    return grid


def tex_log(key):
    rng = seeded(key)
    cols = [rng.choice(["#6b5330", "#5a4526", "#7a6038", "#4c3a20"]) for _ in range(16)]
    return [[c if rng.random() > .15 else "#5a4526" for c in cols] for _ in range(16)]


def tex_block(key, color):
    """A solid block tinted with `color`, bevelled like an item-form block."""
    color = mix(color, "#808080", .12)
    grid = noise(key, [color, lighter(color, .12), darker(color, .12), darker(color, .22)], [6, 2, 2, 1])
    for i in range(16):
        grid[0][i] = grid[i][0] = lighter(color, .38)
    for i in range(16):
        grid[15][i] = grid[i][15] = darker(color, .42)
    return grid


class Canvas:
    def __init__(self, h, label):
        self.h, self.label, self.defs, self.body = h, label, {}, []

    def add(self, s):
        self.body.append(s)

    def rect(self, x, y, w, h, fill, extra=""):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"{extra}/>')

    def tex(self, tid, grid):
        if tid in self.defs:
            return tid
        runs = {}
        for y, row in enumerate(grid):
            x = 0
            while x < 16:
                n = 1
                while x + n < 16 and row[x + n] == row[x]:
                    n += 1
                runs.setdefault(row[x], []).append(f"M{x} {y}h{n}v1h-{n}z")
                x += n
        paths = "".join(f'<path fill="{c}" d="{"".join(d)}"/>' for c, d in runs.items())
        self.defs[tid] = f'<g id="{tid}">{paths}</g>'
        return tid

    def put(self, tid, x, y, scale=3):
        self.add(f'<use href="#{tid}" transform="translate({x} {y}) scale({scale})"/>')

    def text(self, x, y, s, string, fill, shadow=True, anchor="start", outline=None):
        """Draws `string` with its cap-top at y. One font pixel is `s` px; glyphs advance 6."""
        width = text_w(string, s)
        x = round(x - width / 2) if anchor == "middle" else x - width if anchor == "end" else x
        uses = []
        for i, ch in enumerate(string):
            if ch == " ":
                continue
            ch = ch if ch in GLYPHS else "?"
            gid = f"g{ord(ch):x}"
            self.defs[gid] = f'<path id="{gid}" d="{GLYPHS[ch]}"/>'
            uses.append(f'<use href="#{gid}" x="{i * 6}"/>')
        uses = "".join(uses)
        layers = []
        if outline:
            layers += [(outline, dx * s, dy * s) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        elif shadow:
            layers.append((darker(fill, .75), s, s))
        for colour, dx, dy in layers + [(fill, 0, 0)]:
            self.add(f'<g fill="{colour}" transform="translate({x + dx} {y + dy}) scale({s})">{uses}</g>')

    def dirt_backdrop(self, dim=.72):
        for name in ("dirt-a", "dirt-b"):
            self.tex(name, tex_dirt(name))
        for row in range(-(-self.h // BLOCK)):
            for col in range(W // BLOCK):
                self.put("dirt-a" if (row + col) % 2 else "dirt-b", col * BLOCK, row * BLOCK)
        self.rect(0, 0, W, self.h, "#000", f' opacity="{dim}"')

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{self.h}" '
                f'viewBox="0 0 {W} {self.h}" shape-rendering="crispEdges" role="img" '
                f'aria-label="{html.escape(self.label)}"><title>{html.escape(self.label)}</title>'
                f'<defs>{"".join(self.defs.values())}</defs>{"".join(self.body)}</svg>\n')


def text_w(string, s):
    return max(0, len(string) * 6 - 1) * s


def fit(string, chars):
    return string if len(string) <= chars else string[: chars - 1].rstrip() + "…"


def banner(d):
    h = 360
    c = Canvas(h, f'{d["name"]}. {d["bio"]}')
    sky = ["#6f9df7", "#7aa6f8", "#86aff9", "#92b8fa", "#9ec1fb", "#aacafc", "#b6d3fd", "#c2dcfe"]
    for i, colour in enumerate(sky):
        c.rect(0, i * 45, W, 45, colour)
    c.rect(750, 26, 72, 72, "#fff3a8", ' opacity=".55"')
    c.rect(758, 34, 56, 56, "#fffbe0")

    clouds = [(60, 96, [(1, 0, 4), (0, 1, 7), (2, 2, 4)]), (150, 78, [(2, 0, 3), (0, 1, 8)]),
              (20, 120, [(1, 0, 5), (0, 1, 6), (1, 2, 3)])]
    for i, (y, dur, rows) in enumerate(clouds):
        rects = "".join(f'<rect x="{x * 12}" y="{y + r * 12}" width="{w * 12}" height="12"/>' for x, r, w in rows)
        c.add(f'<g fill="#fff" opacity=".8">{rects}<animateTransform attributeName="transform" '
              f'type="translate" from="-120 0" to="{W + 20} 0" dur="{dur}s" begin="-{dur * (i + 1) // 4}s" '
              f'repeatCount="indefinite"/></g>')

    for t in ("grass-a", "grass-b"):
        c.tex(t, tex_grass(t))
    for t in ("dirt-a", "dirt-b"):
        c.tex(t, tex_dirt(t))
    c.tex("log", tex_log("log"))
    c.tex("leaves", noise("leaves", ["#3f7a2a", "#4c9133", "#35681f", "#2c5719"], [4, 3, 2, 1]))
    c.tex("water", noise("water", ["#3f76e4", "#4a80ea", "#386bd2"], [5, 2, 2]))

    heights = [2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3]
    for col, height in enumerate(heights):
        pond = height == 1
        for level in range(height):
            top = level == height - 1 and not pond
            name = ("grass-" if top else "dirt-") + "ab"[(col + level) % 2]
            c.put(name, col * BLOCK, h - (level + 1) * BLOCK)
        if pond:
            c.put("water", col * BLOCK, h - 2 * BLOCK)
            c.rect(col * BLOCK, h - 2 * BLOCK, BLOCK, 6, "#c2dcfe")

    ground = h - 2 * BLOCK
    for cx, cy in [(1, 3), (2, 3), (3, 3), (1, 4), (2, 4), (3, 4), (2, 5)]:
        c.put("leaves", cx * BLOCK, ground - cy * BLOCK)
    for level in (1, 2):
        c.put("log", 2 * BLOCK, ground - level * BLOCK)

    title = d["name"].upper()
    s = max(4, min(12, (W - 380) // (len(title) * 6)))
    y = 40 + (12 - s) * 4
    c.add('<g opacity=".3">')
    c.text(W // 2 + s // 2, y + s + s // 2, s, title, "#000000", shadow=False, anchor="middle")
    c.add("</g>")
    for depth in range(1, 4):
        c.text(W // 2, y + depth * s // 3, s, title, "#4a4a4a", shadow=False, anchor="middle")
    c.defs["stone"] = ('<linearGradient id="stone" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/>'
          '<stop offset="1" stop-color="#b9b9b9"/></linearGradient>')
    c.text(W // 2, y, s, title, "url(#stone)", shadow=False, anchor="middle")

    for i, line in enumerate(textwrap.wrap(d["bio"], 44)[:3]):
        c.text(W // 2, 174 + i * 24, 2, line, "#ffffff", anchor="middle")

    splashes = CONFIG.get("splashes") or [""]
    splash = splashes[datetime.date.today().isocalendar()[1] % len(splashes)]
    if splash:
        edge = W // 2 + text_w(title, s) // 2
        c.add(f'<g transform="translate({min(edge + 30, W - 150)} 132) rotate(-18)"><g>'
              '<animateTransform attributeName="transform" type="scale" values="1;1.07;1" dur="0.9s" '
              'repeatCount="indefinite"/>')
        c.text(0, -7, 2, splash, "#ffff55", anchor="middle")
        c.add("</g></g>")
    return c.render()


def slot(c, x, y, item=None):
    c.rect(x, y, 72, 72, "#373737")
    c.rect(x + 4, y + 4, 68, 68, "#ffffff")
    c.rect(x + 4, y + 4, 64, 64, "#8b8b8b")
    if not item:
        return
    name, abbr, colour, count = item
    tid = c.tex("item-" + re.sub(r"\W", "", name.lower()), tex_block(name, colour))
    c.put(tid, x + 12, y + 12)
    c.text(x + 37, y + 28, 2, abbr, "#ffffff", anchor="middle")
    if count > 1:  # a single item shows no number, same as the game
        c.text(x + 66, y + 50, 2, str(count), "#ffffff", anchor="end")
    c.text(x + 36, y + 82, 2, fit(name, 11), "#3f3f3f", shadow=False, anchor="middle")


def inventory(d):
    items = d["stack"]
    rows = [items[i:i + 6] for i in range(0, len(items), 6)]
    h = 62 + 114 * len(rows) + 10
    stack = ", ".join(i[0] for i in items)
    c = Canvas(h, f"Inventory. Tech stack: {stack}")
    c.rect(4, 0, W - 8, h, "#000")
    c.rect(0, 4, W, h - 8, "#000")
    c.rect(4, 4, W - 8, h - 8, "#555555")
    c.rect(4, 4, W - 12, h - 12, "#ffffff")
    c.rect(8, 8, W - 16, h - 16, "#c6c6c6")
    c.text(PAD, 24, 3, "Inventory", "#3f3f3f", shadow=False)
    for r, row in enumerate(rows):
        for i in range(6):
            slot(c, PAD + i * 136 + 32, 62 + r * 114, row[i] if i < len(row) else None)
    return c.render()


def section(title):
    c = Canvas(64, title)
    c.dirt_backdrop(.55)
    c.text(W // 2, 22, 3, title, "#ffffff", anchor="middle")
    return c.render()


def world(repo, d):
    own = repo["owner"]["login"] == d["login"]
    name = (repo["name"] if own else repo["nameWithOwner"]).strip("-_")
    desc = textwrap.wrap(describe(repo, 116), 58)[:2]
    lang = repo.get("primaryLanguage") or {"name": "", "color": "#8b8b8b"}
    h = 132
    c = Canvas(h, f'{name}: {" ".join(desc)}')
    c.dirt_backdrop()
    c.rect(4, 4, W - 8, h - 8, "none", ' stroke="#808080" stroke-width="2"')

    c.rect(22, 24, 84, 84, "#000")
    c.put(c.tex("icon", tex_block(repo["nameWithOwner"], lang["color"] or "#8b8b8b")), 24, 26, 5)
    c.text(67, 48, 5, name[:1].upper(), "#ffffff", anchor="middle")

    c.text(124, 20, 3, fit(name, 34), "#ffffff")
    if repo["stargazerCount"]:
        c.text(W - PAD, 23, 2, f'★ {repo["stargazerCount"]}', "#ffff55", anchor="end")
    for i, line in enumerate(desc):
        c.text(124, 54 + i * 22, 2, line, "#aaaaaa")

    tags = ([] if own else ["Contributor"]) + [lang["name"]] * bool(lang["name"]) + [t[0] for t in techs(repo)[:2]]
    x = 124
    if lang["name"]:
        c.rect(x, 104, 14, 14, lang["color"] or "#8b8b8b")
        x += 24
    c.text(x, 104, 2, fit(" · ".join(tags), 40), "#8c8c8c")
    pushed = datetime.datetime.fromisoformat(repo["pushedAt"].replace("Z", "+00:00"))
    c.text(W - PAD, 104, 2, f"{pushed.day} {pushed:%b %Y}", "#8c8c8c", anchor="end")
    return c.render()


def statistics(d):
    rows = [("Contributions, last 12 months", f'{d["contributions"]:,}'),
            ("Public repositories", str(d["repo_count"]))]
    if d["stars"]:
        rows.append(("Stars earned", f'{d["stars"]:,}'))
    if d["top_language"]:
        rows.append(("Most used language", d["top_language"]))
    bar_y = 64 + 32 * len(rows) + 46
    h = bar_y + 20 + 28
    c = Canvas(h, "Statistics. " + ", ".join(f"{k}: {v}" for k, v in rows))
    c.dirt_backdrop()
    c.text(W // 2, 22, 3, "Statistics", "#ffffff", anchor="middle")
    for i, (key, value) in enumerate(rows):
        if i % 2 == 0:
            c.rect(PAD, 64 + i * 32, W - 2 * PAD, 32, "#ffffff", ' opacity=".06"')
        c.text(PAD + 16, 72 + i * 32, 2, key, "#ffffff")
        c.text(W - PAD - 16, 72 + i * 32, 2, value, "#ffff55", anchor="end")

    # XP bar: the level is contributions this year, the fill is progress to the next hundred.
    x, w = 68, 728
    c.rect(x - 4, bar_y - 4, w + 8, 28, "#000")
    c.rect(x, bar_y, w, 20, "#2f2f2f")
    filled = round(w * (d["contributions"] % 100) / 100 / 4) * 4
    c.rect(x, bar_y, filled, 20, "#7ee030")
    c.rect(x, bar_y, filled, 4, "#b9f77c")
    c.rect(x, bar_y + 16, filled, 4, "#4e9a1a")
    for i in range(1, 18):
        c.rect(x + i * 40 + 2, bar_y, 4, 20, "#000", ' opacity=".4"')
    c.text(W // 2, bar_y - 30, 3, str(d["contributions"]), "#80ff20", anchor="middle", outline="#000000")
    return c.render()


def button(label):
    c = Canvas(48, label)
    w = 272
    c.rect(0, 0, w, 48, "#000")
    c.rect(4, 4, w - 8, 40, "#6f6f6f")
    c.rect(4, 4, w - 8, 4, "#aaaaaa")
    c.rect(4, 4, 4, 36, "#aaaaaa")
    c.rect(4, 36, w - 8, 8, "#565656")
    c.text(w // 2, 16, 2, fit(label, 20), "#ffffff", anchor="middle")
    return c.render().replace(f'width="{W}"', f'width="{w}"', 1).replace(f"0 0 {W} 48", f"0 0 {w} 48", 1)


# ---------------------------------------------------------------- output

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build(user):
    d = summarise(user)
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("*.svg"):
        old.unlink()

    def save(name, svg):
        (ASSETS / name).write_text(svg, encoding="utf-8", newline="\n")
        return f"assets/{name}"

    def img(src, alt, width="100%"):
        return f'<img src="{src}" width="{width}" alt="{html.escape(alt, quote=True)}">'

    stack = ", ".join(i[0] for i in d["stack"])
    out = ["<!-- Generated by scripts/build.py. Change config.json or the script, not this file. -->", "",
           f'<p align="center">{img(save("banner.svg", banner(d)), d["name"] + ". " + d["bio"])}</p>', ""]
    if stack:
        out += [f'<p align="center">{img(save("inventory.svg", inventory(d)), "Tech stack: " + stack)}</p>', ""]
    if d["worlds"]:
        out.append('<p align="center">')
        out.append(img(save("worlds.svg", section("Select World")), "Projects"))
        for repo in d["worlds"]:
            src = save(f'world-{slug(repo["nameWithOwner"].split("/", 1)[1] if repo["owner"]["login"] == d["login"] else repo["nameWithOwner"])}.svg', world(repo, d))
            alt = f'{repo["name"].strip("-_")}: {describe(repo, 116)}'
            out.append(f'<a href="{html.escape(repo["url"], quote=True)}">{img(src, alt)}</a>')
        out += ["</p>", ""]
    stats_alt = f'{d["contributions"]} contributions in the last 12 months, {d["repo_count"]} public repositories'
    out += [f'<p align="center">{img(save("statistics.svg", statistics(d)), stats_alt)}</p>', ""]
    if d["links"]:
        out.append('<p align="center">')
        for label, url in d["links"]:
            src = save(f"button-{slug(label)}.svg", button(label))
            out.append(f'<a href="{html.escape(url, quote=True)}">{img(src, label, 272)}</a>')
        out += ["</p>", ""]
    out += ['<p align="center"><sub>Rebuilt daily from live GitHub data by <a href="scripts/build.py">a script</a>. '
            'Pixel font: <a href="https://github.com/IdreesInc/Monocraft">Monocraft</a>, OFL.</sub></p>', ""]
    (ROOT / "README.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f'Built README for {d["login"]}: {len(d["worlds"])} worlds, stack: {stack}')


if __name__ == "__main__":
    login = CONFIG.get("login") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if "--data" in sys.argv:
        saved = json.loads(Path(sys.argv[sys.argv.index("--data") + 1]).read_text(encoding="utf-8"))
        build(saved["data"]["user"] if "data" in saved else saved)
    else:
        build(fetch(login))
