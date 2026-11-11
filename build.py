#!/usr/bin/env python3
"""GitHub profile dashboard for TRMNL OG (800x480, landscape, 1-bit).

Fetches a GitHub profile (GraphQL + REST), computes the stats, renders an
HTML page at exactly 800x480 in pure black & white, then screenshots it into
a 1-bit PNG. Output goes to ./site (served by GitHub Pages).

Environment:
  GH_LOGIN    GitHub login to display (default: repository owner)
  GH_TOKEN    token used for the API (GITHUB_TOKEN or a personal token)
  TZ_NAME     time zone for "Updated" and "Hour of day" (default Europe/Paris)
  MOCK=1      use fictitious data (local preview, no network)
"""
import base64, datetime as dt, html, io, json, os, random, sys, urllib.request
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
LOGIN = os.environ.get("GH_LOGIN") or "nbbou81000"
TOKEN = os.environ.get("GH_TOKEN", "")
TZ = ZoneInfo(os.environ.get("TZ_NAME") or "Europe/Paris")
NOW = dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- API
def _req(url, body=None):
    headers = {"User-Agent": "trmnl-github-dashboard", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=60) as r:
        return r.read()


def gql(query, **variables):
    res = json.loads(_req("https://api.github.com/graphql", {"query": query, "variables": variables}))
    if res.get("errors"):
        raise RuntimeError(json.dumps(res["errors"])[:800])
    return res["data"]


def fetch_real(login):
    base = gql("""
    query($login:String!, $merged:String!, $prs:String!){
      user(login:$login){
        name login bio location websiteUrl createdAt isHireable avatarUrl(size:200)
        following{totalCount} followers{totalCount}
        pullRequests{totalCount} issues{totalCount}
        repositories(ownerAffiliations:OWNER, privacy:PUBLIC){totalCount}
        contributionsCollection{
          contributionYears
          contributionCalendar{ totalContributions
            weeks{ contributionDays{ date contributionCount contributionLevel } } }
        }
      }
      merged: search(query:$merged, type:ISSUE, first:0){ issueCount }
      prs: search(query:$prs, type:ISSUE, first:4){ nodes{ ... on PullRequest{
        title number state merged createdAt mergedAt closedAt repository{ name } } } }
    }""", login=login, merged=f"author:{login} is:pr is:merged",
        prs=f"author:{login} is:pr sort:created-desc")
    u = base["user"]
    if not u:
        raise RuntimeError(f"GitHub user '{login}' not found")

    # One calendar per contribution year (all-time streaks, commits, by-month).
    years = sorted(u["contributionsCollection"]["contributionYears"])
    parts = []
    for y in years:
        parts.append(f'y{y}: contributionsCollection(from:"{y}-01-01T00:00:00Z", to:"{y}-12-31T23:59:59Z"){{'
                     " totalCommitContributions restrictedContributionsCount"
                     " contributionCalendar{ weeks{ contributionDays{ date contributionCount } } } }")
    ydata = gql("query($login:String!){ user(login:$login){ %s } }" % " ".join(parts), login=login)["user"]
    days, commits = {}, 0
    for y in years:
        c = ydata[f"y{y}"]
        commits += c["totalCommitContributions"]
        for w in c["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    # Repositories (owned, public, not forks), paginated.
    repos, cursor = [], None
    while True:
        r = gql("""query($login:String!, $after:String){ user(login:$login){
          repositories(first:100, after:$after, ownerAffiliations:OWNER, privacy:PUBLIC, isFork:false,
                       orderBy:{field:STARGAZERS, direction:DESC}){
            pageInfo{ hasNextPage endCursor }
            nodes{ name description stargazerCount forkCount pushedAt primaryLanguage{ name }
                   repositoryTopics(first:20){ nodes{ topic{ name } } } } } } }""",
                login=login, after=cursor)["user"]["repositories"]
        for n in r["nodes"]:
            repos.append({
                "name": n["name"], "description": n["description"] or "",
                "stars": n["stargazerCount"], "forks": n["forkCount"], "pushed_at": n["pushedAt"],
                "language": (n["primaryLanguage"] or {}).get("name"),
                "topics": [t["topic"]["name"] for t in n["repositoryTopics"]["nodes"]],
            })
        if not r["pageInfo"]["hasNextPage"] or len(repos) >= 1000:
            break
        cursor = r["pageInfo"]["endCursor"]

    try:
        events = json.loads(_req(f"https://api.github.com/users/{login}/events/public?per_page=100"))
    except Exception as e:  # events are a nice-to-have
        print("events:", e, file=sys.stderr)
        events = []

    avatar = None
    try:
        avatar = _req(u["avatarUrl"])
    except Exception as e:
        print("avatar:", e, file=sys.stderr)

    return {
        "profile": {
            "name": u["name"] or u["login"], "login": u["login"], "bio": u["bio"] or "",
            "location": u["location"] or "", "website": u["websiteUrl"] or "",
            "created_at": u["createdAt"], "hireable": u["isHireable"],
            "following": u["following"]["totalCount"], "followers": u["followers"]["totalCount"],
        },
        "public_repos": u["repositories"]["totalCount"],
        "prs_total": u["pullRequests"]["totalCount"], "prs_merged": base["merged"]["issueCount"],
        "issues_total": u["issues"]["totalCount"], "commits_total": commits,
        "calendar": u["contributionsCollection"]["contributionCalendar"]["weeks"],
        "days": days, "repos": repos,
        "latest_prs": [p for p in base["prs"]["nodes"] if p],
        "events": events, "avatar": avatar,
    }


# -------------------------------------------------------------------------- mock
def fetch_mock(login):
    rnd = random.Random(7)
    today = NOW.astimezone(TZ).date()
    start = today - dt.timedelta(days=700)
    days = {}
    d = start
    while d <= today:
        age = (today - d).days
        active = rnd.random() < (0.85 if age < 190 else 0.15)
        days[d.isoformat()] = (rnd.randint(1, 40) if rnd.random() < .9 else rnd.randint(40, 95)) if active else 0
        d += dt.timedelta(days=1)
    # last-year calendar: 53 weeks starting on a Sunday
    first = today - dt.timedelta(days=364)
    first -= dt.timedelta(days=(first.weekday() + 1) % 7)
    weeks, d = [], first
    while d <= today:
        wk = []
        for _ in range(7):
            if d > today:
                break
            c = days.get(d.isoformat(), 0)
            lvl = "NONE" if c == 0 else ["FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"][min(3, c // 12)]
            wk.append({"date": d.isoformat(), "contributionCount": c, "contributionLevel": lvl})
            d += dt.timedelta(days=1)
        weeks.append({"contributionDays": wk})
    names = [("trmnl-patents", "US patent drawings rendered for e-ink, a new device every refresh", "Python", 412, 31),
             ("emojipedia", "Emoji encyclopedia and emoji rebus puzzles for TRMNL", "JavaScript", 188, 12),
             ("petrol", "WTI and Brent oil prices with 30-session charts for e-ink", "JavaScript", 97, 6),
             ("trmnl-creator-stats", "Creator statistics for TRMNL recipe authors", "JavaScript", 64, 4),
             ("Ngram", "Google Books Ngram word duels", "Python", 22, 1), ("AMBER-alert", "", "JavaScript", 18, 2)]
    repos = []
    for i, (n, desc, lang, s, f) in enumerate(names):
        repos.append({"name": n, "description": desc, "stars": s, "forks": f, "language": lang,
                      "pushed_at": (NOW - dt.timedelta(hours=[9, 30, 100, 400, 900, 2000][i])).isoformat(),
                      "topics": ["trmnl", "e-ink", "dashboard"][: 3 - i % 3] + (["github-actions"] if i % 2 else [])})
    for i in range(40):
        repos.append({"name": f"repo-{i}", "description": "", "stars": rnd.randint(0, 8), "forks": rnd.randint(0, 2),
                      "language": rnd.choice(["JavaScript"] * 5 + ["Python"] * 3 + ["HTML", "CSS", "Liquid", "Shell"]),
                      "pushed_at": NOW.isoformat(), "topics": rnd.sample(["trmnl", "e-ink", "plugin", "liquid", "github-pages"], 2)})
    ev = []
    kinds = [("IssuesEvent", {"action": "opened", "issue": {"number": 86}}), ("IssuesEvent", {"action": "labeled", "issue": {"number": 86}}),
             ("ReleaseEvent", {"action": "published", "release": {"tag_name": "v1.4.0"}}),
             ("PullRequestEvent", {"action": "closed", "pull_request": {"number": 85, "merged": True}}),
             ("PushEvent", {"ref": "refs/heads/main"}), ("PullRequestEvent", {"action": "opened", "pull_request": {"number": 85}})]
    for i in range(100):
        k, p = kinds[i % len(kinds)]
        t = NOW - dt.timedelta(hours=8 + i * 3.7 + rnd.random() * 3)
        ev.append({"type": k, "repo": {"name": f"{login}/{['trmnl-patents', 'petrol', 'emojipedia'][i % 3]}"},
                   "payload": p, "created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ")})
    prs = [{"title": t, "number": n, "state": "MERGED", "merged": True, "repository": {"name": r},
            "createdAt": (NOW - dt.timedelta(hours=h)).isoformat(), "mergedAt": (NOW - dt.timedelta(hours=h)).isoformat()}
           for t, n, r, h in [("fix(render): keep the plate centered on TRMNL X", 85, "trmnl-patents", 9),
                              ("feat(rebus): add three difficulty levels", 70, "emojipedia", 9),
                              ("feat(chart): 30-session sparkline for Brent", 64, "petrol", 11),
                              ("test(stats): deflake the hourly refresh test", 63, "trmnl-creator-stats", 19)]]
    return {
        "profile": {"name": "Nicolas Bouteiller", "login": login, "bio": "Building e-ink plugins for TRMNL. Author of La Bible Geek.",
                    "location": "Albi, France", "website": "ko-fi.com/nicolasbouteiller", "created_at": "2024-09-01T00:00:00Z",
                    "hireable": False, "following": 17, "followers": 47},
        "public_repos": 64, "prs_total": 290, "prs_merged": 275, "issues_total": 106, "commits_total": 7020,
        "calendar": weeks, "days": days, "repos": repos, "latest_prs": prs, "events": ev, "avatar": None,
    }


# ----------------------------------------------------------------------- compute
def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def ago(s):
    if not s:
        return ""
    sec = (NOW - parse_ts(s)).total_seconds()
    if sec < 3600:
        return f"{max(1, int(sec // 60))}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    return f"{int(sec // 86400)}d ago"


def fmt(n):
    return f"{n:,}"


def compute(raw):
    today = NOW.astimezone(TZ).date()
    days = {dt.date.fromisoformat(k): v for k, v in raw["days"].items() if dt.date.fromisoformat(k) <= today}
    year = today.year

    # Streaks (all time). Current streak tolerates "nothing yet today".
    cur, d = 0, today
    if days.get(d, 0) == 0:
        d -= dt.timedelta(days=1)
    while days.get(d, 0) > 0:
        cur += 1
        d -= dt.timedelta(days=1)
    longest = run = 0
    for k in sorted(days):
        run = run + 1 if days[k] > 0 else 0
        longest = max(longest, run)
    best_day = max(days.items(), key=lambda kv: (kv[1], kv[0])) if days else (today, 0)

    last365 = [today - dt.timedelta(days=i) for i in range(365)]
    total12 = sum(days.get(x, 0) for x in last365)
    active = sum(1 for x in last365 if days.get(x, 0) > 0)
    last7 = sum(days.get(today - dt.timedelta(days=i), 0) for i in range(7))
    year_total = sum(v for k, v in days.items() if k.year == year)

    # Calendar (last ~53 weeks)
    weeks = []
    for w in raw["calendar"]:
        weeks.append([(dt.date.fromisoformat(x["date"]), x["contributionCount"], x.get("contributionLevel", "NONE"))
                      for x in w["contributionDays"]])
    weekly = [sum(c for _, c, _ in w) for w in weeks]
    months_lbl = []
    for i, w in enumerate(weeks):
        first = w[0][0]
        if i == 0 or first.month != weeks[i - 1][0][0].month:
            if i < len(weeks) - 2:
                months_lbl.append((i, first.strftime("%b")))
    if len(months_lbl) > 1 and months_lbl[1][0] - months_lbl[0][0] < 3:
        months_lbl.pop(0)

    by_wd = [0] * 7  # Sun..Sat
    for x in last365:
        by_wd[(x.weekday() + 1) % 7] += days.get(x, 0)
    by_month = [sum(v for k, v in days.items() if k.year == year and k.month == m) for m in range(1, 13)]

    repos = raw["repos"]
    stars = sum(r["stars"] for r in repos)
    forks = sum(r["forks"] for r in repos)
    top = sorted(repos, key=lambda r: (-r["stars"], r["name"]))[:4]
    langs = Counter(r["language"] for r in repos if r["language"])
    topics = Counter(t for r in repos for t in r["topics"])

    hours = [0] * 24
    for e in raw["events"][:100]:
        hours[parse_ts(e["created_at"]).astimezone(TZ).hour] += 1

    activity = [describe_event(e) for e in raw["events"]]
    activity = [a for a in activity if a][:6]

    prs = []
    for p in raw["latest_prs"][:4]:
        state = "merged" if p.get("merged") else p["state"].lower()
        when = p.get("mergedAt") or p.get("closedAt") or p["createdAt"]
        prs.append({"title": p["title"], "repo": p["repository"]["name"], "number": p["number"],
                    "state": state, "ago": ago(when)})

    prof = raw["profile"]
    return {
        "p": prof, "joined": parse_ts(prof["created_at"]).strftime("%b %Y"),
        "updated": NOW.astimezone(TZ).strftime("%-d %b, %H:%M"),
        "year": year, "year_total": year_total, "cur": cur, "longest": longest,
        "best": best_day[1], "best_date": best_day[0].strftime("%-d %b %Y"),
        "avg": total12 / 365, "active": active, "last7": last7, "total12": total12,
        "weeks": weeks, "weekly": weekly, "months_lbl": months_lbl,
        "tiles": [("repo", raw["public_repos"], "Public repos"), ("star", stars, "Stars earned"),
                  ("fork", forks, "Forks"), ("people", prof["followers"], "Followers"),
                  ("commit", raw["commits_total"], "Commits"), ("pr", raw["prs_total"], "Pull requests"),
                  ("merge", raw["prs_merged"], "Merged PRs"), ("issue", raw["issues_total"], "Issues opened")],
        "top": top, "langs": langs, "by_wd": by_wd, "by_month": by_month, "hours": hours,
        "topics": topics.most_common(6), "activity": activity, "prs": prs, "avatar": raw.get("avatar"),
    }


def describe_event(e):
    t, p, repo = e["type"], e.get("payload", {}), e["repo"]["name"].split("/")[-1]
    a = p.get("action")
    if t == "IssuesEvent":
        n = p.get("issue", {}).get("number", "")
        text, icon = {"opened": ("Opened issue", "issue"), "closed": ("Closed issue", "issue-closed"),
                      "labeled": ("Labeled issue", "tag"), "reopened": ("Reopened issue", "issue")}.get(a, (f"{(a or '').capitalize()} issue", "issue"))
        text += f" #{n}"
    elif t == "PullRequestEvent":
        pr = p.get("pull_request", {})
        n = pr.get("number") or p.get("number", "")
        if a == "closed" and pr.get("merged"):
            text, icon = f"Merged PR #{n}", "merge"
        else:
            text, icon = f"{(a or '').capitalize()} PR #{n}", "pr"
    elif t == "ReleaseEvent":
        text, icon = f"Released {p.get('release', {}).get('tag_name', '')}", "rocket"
    elif t == "PushEvent":
        text, icon = f"Pushed to {p.get('ref', '').split('/')[-1] or 'branch'}", "commit"
    elif t == "CreateEvent":
        text, icon = f"Created {p.get('ref_type', '')} {p.get('ref') or ''}".strip(), "plus"
    elif t == "WatchEvent":
        text, icon = "Starred", "star"
    elif t == "ForkEvent":
        text, icon = "Forked", "fork"
    elif t == "IssueCommentEvent":
        text, icon = f"Commented #{p.get('issue', {}).get('number', '')}", "comment"
    elif t == "PullRequestReviewEvent":
        text, icon = f"Reviewed PR #{p.get('pull_request', {}).get('number', '')}", "eye"
    else:
        return None
    return {"icon": icon, "text": text, "repo": repo, "ago": ago(e["created_at"])}


# ------------------------------------------------------------------------ icons
I = {
    "repo": '<path d="M3 2.5h8.5v10H4.2a1.2 1.2 0 0 0 0 2.4H13M3 2.5v11.6M5.5 5.5h4"/>',
    "star": '<path d="M8 1.6l1.9 4 4.3.5-3.2 3 .9 4.3L8 11.2l-3.9 2.2.9-4.3-3.2-3 4.3-.5z"/>',
    "fork": '<circle cx="4.5" cy="3.2" r="1.6"/><circle cx="11.5" cy="3.2" r="1.6"/><circle cx="8" cy="12.8" r="1.6"/><path d="M4.5 4.8v1.4c0 1.2 1 2 2 2h3c1 0 2-.8 2-2V4.8M8 8.2v3"/>',
    "people": '<circle cx="6" cy="5" r="2.5"/><path d="M1.5 14c0-2.6 2-4.5 4.5-4.5s4.5 1.9 4.5 4.5M10.5 2.7a2.5 2.5 0 0 1 0 4.6M12 9.8c1.6.6 2.6 2.2 2.6 4.2"/>',
    "commit": '<circle cx="8" cy="8" r="2.6"/><path d="M1 8h4.4M10.6 8H15"/>',
    "pr": '<circle cx="4" cy="3.3" r="1.6"/><circle cx="4" cy="12.7" r="1.6"/><circle cx="12" cy="12.7" r="1.6"/><path d="M4 4.9v6.2M12 11.1V6c0-1.2-.8-2-2-2H7.5M9 2.3L7.3 4 9 5.7"/>',
    "merge": '<circle cx="4" cy="3.3" r="1.6"/><circle cx="4" cy="12.7" r="1.6"/><circle cx="12" cy="8.5" r="1.6"/><path d="M4 4.9v6.2M4.4 5c.8 2.3 2.8 3.5 6 3.5"/>',
    "issue": '<circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/>',
    "issue-closed": '<circle cx="8" cy="8" r="6"/><path d="M5.5 8l1.8 1.8 3.2-3.4"/>',
    "tag": '<path d="M1.8 2.6v4.6l6.8 6.8 5.4-5.4-6.8-6.8H2.6z"/><circle cx="5" cy="5" r="1"/>',
    "rocket": '<path d="M9.5 2.5c2-1 4-1 4-1s0 2-1 4L8 10 6 8zM6 8l-3-.5 2-2.5h3M8 10l.5 3 2.5-2V8M4.5 11.5l-2 2"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "comment": '<path d="M2 3h12v8H7l-3 2.5V11H2z"/>',
    "eye": '<path d="M1 8s2.6-4.5 7-4.5S15 8 15 8s-2.6 4.5-7 4.5S1 8 1 8z"/><circle cx="8" cy="8" r="2"/>',
    "pin": '<path d="M8 15s-4.5-4.6-4.5-8a4.5 4.5 0 0 1 9 0c0 3.4-4.5 8-4.5 8z"/><circle cx="8" cy="7" r="1.6"/>',
    "link": '<path d="M7 9a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2L8.6 3.2M9 7a3 3 0 0 0-4.2 0L2.5 9.3a3 3 0 0 0 4.2 4.2l.7-.7"/>',
    "cal": '<rect x="2" y="3" width="12" height="11" rx="1"/><path d="M2 6.5h12M5 1.5v3M11 1.5v3"/>',
    "follow": '<circle cx="6" cy="5" r="2.6"/><path d="M1.5 14c0-2.6 2-4.5 4.5-4.5 1.2 0 2.2.4 3 1M12 9v5M9.5 11.5h5"/>',
    "bag": '<rect x="1.5" y="4.5" width="13" height="9" rx="1"/><path d="M5.5 4.5V2.5h5v2M1.5 8.5h13"/>',
    "grid": '<rect x="2" y="2" width="5" height="5"/><rect x="9" y="2" width="5" height="5"/><rect x="2" y="9" width="5" height="5"/><rect x="9" y="9" width="5" height="5"/>',
    "code": '<path d="M5 4L1.5 8 5 12M11 4l3.5 4-3.5 4M9.3 2.5l-2.6 11"/>',
    "pulse": '<path d="M1 8.5h3l2-5 3.5 9 2-4H15"/>',
    "clock": '<circle cx="8" cy="8" r="6.2"/><path d="M8 4.5V8l2.5 1.6"/>',
    "bars": '<path d="M2 14h12M3.5 14V9M7 14V3.5M10.5 14V6.5"/>',
    "book": '<rect x="2" y="2" width="12" height="12" rx="1"/><path d="M5 2v12M7.5 5.5h4M7.5 8h4"/>',
}


def icon(name, size=12, cls=""):
    return (f'<svg class="ic {cls}" width="{size}" height="{size}" viewBox="0 0 16 16" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{I[name]}</svg>')


# ----------------------------------------------------------------------- render
# 1-bit fills: pattern ids used by the heatmap, languages and legend.
PATTERNS = """
<svg width="0" height="0" style="position:absolute"><defs>
 <pattern id="p0" width="3" height="3" patternUnits="userSpaceOnUse"><rect width="3" height="3" fill="#fff"/><rect x="1" y="1" width="1" height="1" fill="#000"/></pattern>
 <pattern id="p1" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="2" height="2" fill="#fff"/><rect width="1" height="1" fill="#000"/></pattern>
 <pattern id="p2" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="2" height="2" fill="#fff"/><rect width="1" height="1" fill="#000"/><rect x="1" y="1" width="1" height="1" fill="#000"/></pattern>
 <pattern id="p3" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="2" height="2" fill="#000"/><rect width="1" height="1" fill="#fff"/></pattern>
 <pattern id="p4" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="2" height="2" fill="#000"/></pattern>
</defs></svg>"""
LEVEL = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def swatch(pid, w=9, h=9):
    return (f'<svg width="{w}" height="{h}" class="sw"><rect x=".5" y=".5" width="{w-1}" height="{h-1}" '
            f'fill="url(#{pid})" stroke="#000" stroke-width="1"/></svg>')


def heatmap(vm):
    pitch, cell, left, top_bars, bars_h, top_lbl = 7, 6, 22, 0, 30, 33
    weeks = vm["weeks"]
    W = left + len(weeks) * pitch
    top_cells = top_lbl + 11
    H = top_cells + 7 * pitch
    peak = max(vm["weekly"] or [1]) or 1
    s = [f'<svg width="{W}" height="{H}" shape-rendering="crispEdges">']
    s.append(f'<line x1="{left}" y1="{bars_h + .5}" x2="{W}" y2="{bars_h + .5}" stroke="#000"/>')
    for i, v in enumerate(vm["weekly"]):
        h = round(v / peak * (bars_h - 2))
        if h:
            s.append(f'<rect x="{left + i * pitch}" y="{bars_h - h}" width="{cell}" height="{h}" fill="#000"/>')
    for i, m in vm["months_lbl"]:
        s.append(f'<text x="{left + i * pitch}" y="{top_lbl + 7}" class="t7">{m}</text>')
    for r, lbl in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        s.append(f'<text x="0" y="{top_cells + r * pitch + 6}" class="t7">{lbl}</text>')
    for i, w in enumerate(weeks):
        for d, c, lvl in w:
            r = (d.weekday() + 1) % 7
            x, y = left + i * pitch, top_cells + r * pitch
            s.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="url(#p{LEVEL.get(lvl, 0)})"/>')
    s.append("</svg>")
    return "".join(s), peak


def minibars(values, labels, w, h, show_values=False, label_every=1):
    n = len(values)
    peak = max(values) or 1
    gap = 2 if n > 12 else 4
    bw = (w - gap * (n - 1)) / n
    top = 9 if show_values else 0
    ch = h - 10 - top
    s = [f'<svg width="{w}" height="{h}" shape-rendering="crispEdges">']
    for i, v in enumerate(values):
        x = round(i * (bw + gap))
        bh = round(v / peak * ch) if v else 0
        if bh:
            s.append(f'<rect x="{x}" y="{top + ch - bh}" width="{round(bw)}" height="{bh}" fill="#000"/>')
        else:
            s.append(f'<rect x="{x}" y="{top + ch - 1}" width="{round(bw)}" height="1" fill="#000"/>')
        cx = x + bw / 2
        if show_values:
            s.append(f'<text x="{cx}" y="7" class="t7" text-anchor="middle">{v}</text>')
        if labels[i] and i % label_every == 0:
            s.append(f'<text x="{cx}" y="{h - 1}" class="t7" text-anchor="middle">{labels[i]}</text>')
    s.append("</svg>")
    return "".join(s)


def avatar_tag(vm):
    data = vm.get("avatar")
    if not data:
        initials = "".join(w[0] for w in vm["p"]["name"].split()[:2]).upper()
        return f'<div class="avatar ph">{html.escape(initials)}</div>'
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data)).convert("L").resize((52, 52), Image.LANCZOS)
        im = ImageOps.autocontrast(im).convert("1")  # Floyd-Steinberg dither
        buf = io.BytesIO()
        im.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f'<img class="avatar" src="data:image/png;base64,{b64}">'
    except Exception as e:
        print("avatar render:", e, file=sys.stderr)
        return '<div class="avatar ph">?</div>'


def e(s):
    return html.escape(str(s))


def render(vm):
    p = vm["p"]
    meta = []
    if p["location"]:
        meta.append(f'{icon("pin", 10)}{e(p["location"])}')
    if p["website"]:
        meta.append(f'{icon("link", 10)}{e(p["website"].replace("https://", "").replace("http://", "").rstrip("/"))}')
    meta.append(f'{icon("cal", 10)}Joined {vm["joined"]}')
    meta.append(f'{icon("follow", 10)}Following {p["following"]}')
    if p["hireable"]:
        meta.append(f'{icon("bag", 10)}Open to work')

    stats = [
        (f'{vm["cur"]}<small>days</small>', "Current streak", "and counting" if vm["cur"] else "start one today"),
        (f'{vm["longest"]}<small>days</small>', "Longest streak", "all time"),
        (fmt(vm["best"]), "Best day", vm["best_date"]),
        (f'{vm["avg"]:.1f}', "Daily average", "last 12 months"),
        (f'{vm["active"]}<small>/365</small>', "Active days", f'{round(vm["active"] / 3.65)}% of days'),
        (fmt(vm["last7"]), "Last 7 days", f'{fmt(vm["total12"])} in 12 months'),
    ]
    stats_html = "".join(f'<div class="stat"><b>{v}</b><i>{l}</i><span>{s}</span></div>' for v, l, s in stats)

    hm, peak = heatmap(vm)
    legend = "".join(swatch(f"p{i}", 8, 8) for i in range(5))

    tiles = "".join(f'<div class="tile">{icon(ic, 14)}<div><b>{fmt(v)}</b><i>{l}</i></div></div>'
                    for ic, v, l in vm["tiles"])

    # Languages (by repo), top 5 + Other
    total_l = sum(vm["langs"].values()) or 1
    top_l = vm["langs"].most_common(5)
    other = total_l - sum(c for _, c in top_l)
    lang_items = top_l + ([("Other", other)] if other else [])
    fills = ["p4", "p2", "p3", "p1", "p0", "white"]
    seg, x, W = [], 0, 236
    for i, (n, c) in enumerate(lang_items):
        w = W * c / total_l if i < len(lang_items) - 1 else W - x
        f = "#fff" if fills[i] == "white" else f"url(#{fills[i]})"
        seg.append(f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="12" fill="{f}" stroke="#000" stroke-width="1"/>')
        x += w
    lang_bar = f'<svg width="{W + 1}" height="13" class="langbar">{"".join(seg)}</svg>'
    lang_legend = "".join(
        f'<div class="lg">{swatch(fills[i]) if fills[i] != "white" else swatch("none")}<span>{e(n)}</span><em>{c}</em></div>'
        for i, (n, c) in enumerate(lang_items))

    # Top repositories
    max_s = max([r["stars"] for r in vm["top"]] + [1])
    repos = "".join(f'''<div class="repo"><div class="rk">{i + 1}</div><div class="rb">
        <div class="rl"><b>{e(r["name"])}</b><span class="rs">{icon("star", 9)}{fmt(r["stars"])} {icon("fork", 9)}{fmt(r["forks"])}</span></div>
        <div class="rl"><span class="rd">{e(r["description"] or "No description")}</span><span class="rm">{e(r["language"] or "")}{" · " if r["language"] else ""}{ago(r["pushed_at"])}</span></div>
        <div class="rbar"><div style="width:{max(2, round(r["stars"] / max_s * 100))}%"></div></div></div></div>'''
                    for i, r in enumerate(vm["top"]))

    acts = "".join(f'<div class="act">{icon(a["icon"], 11)}<b>{e(a["text"])}</b><span class="mono">{e(a["repo"])}</span><em>{a["ago"]}</em></div>'
                   for a in vm["activity"]) or '<div class="empty">No recent public activity</div>'
    prs = "".join(f'<div class="pri">{icon("merge" if pr["state"] == "merged" else "pr", 11)}<div><b>{e(pr["title"])}</b>'
                  f'<span class="mono">{e(pr["repo"])} #{pr["number"]} · {pr["state"]} · {pr["ago"]}</span></div></div>'
                  for pr in vm["prs"]) or '<div class="empty">No pull requests yet</div>'

    wd = minibars(vm["by_wd"], ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], 180, 50, show_values=True)
    hr_lbl = ["12a", "", "", "3a", "", "", "6a", "", "", "9a", "", "", "12p", "", "", "3p", "", "", "6p", "", "", "9p", "", ""]
    hr = minibars(vm["hours"], hr_lbl, 180, 50)
    mo = minibars(vm["by_month"], list("JFMAMJJASOND"), 176, 50)
    topics = "".join(f'<span class="chip">{e(t)} <em>{c}</em></span>' for t, c in vm["topics"])

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@500;600;700;800&family=Azeret+Mono:wght@600;700;800&display=block" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:800px;height:480px;overflow:hidden;background:#fff;color:#000}}
body{{font-family:'Inter Tight',Inter,sans-serif;-webkit-font-smoothing:none;font-smooth:never}}
.mono,.t7,i,small,em,.meta,.h{{font-family:'Azeret Mono',monospace}}
.t7{{font-size:7px;font-weight:700;fill:#000}}
.ic{{flex:none;display:inline-block;vertical-align:-1px}}
.sw{{flex:none;display:inline-block}}
/* header */
.hd{{position:absolute;left:0;top:0;width:800px;height:62px;background:#000;color:#fff;display:flex;align-items:center;padding:0 12px;gap:12px}}
.avatar{{width:52px;height:52px;border-radius:50%;border:2px solid #fff;image-rendering:pixelated;flex:none;outline:2px solid #000;outline-offset:-4px}}
.avatar.ph{{display:flex;align-items:center;justify-content:center;font:800 18px 'Azeret Mono',monospace}}
.who{{flex:1;min-width:0}}
.who h1{{font-size:26px;font-weight:800;letter-spacing:-.8px;line-height:26px;white-space:nowrap}}
.who .l2{{display:flex;gap:8px;align-items:baseline;font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;margin-top:1px}}
.who .l2 .mono{{font-size:10px;font-weight:700;flex:none}}
.who .l2 span:last-child{{overflow:hidden;text-overflow:ellipsis;min-width:0}}
.meta{{display:flex;gap:11px;font-size:8.5px;font-weight:700;margin-top:3px;white-space:nowrap}}
.meta span{{display:flex;gap:3px;align-items:center}}
.upd{{text-align:right;font:700 8px/1.3 'Azeret Mono',monospace;align-self:flex-end;margin-bottom:8px}}
.upd b{{font:800 12px/1 'Inter Tight';display:block;margin-bottom:3px;letter-spacing:.2px}}
/* sections */
.sec{{position:absolute}}
.h{{display:flex;align-items:center;gap:5px;font-size:8.5px;font-weight:800;letter-spacing:1.3px;text-transform:uppercase;border-top:2px solid #000;padding-top:3px;height:15px;margin-bottom:3px}}
.h .r{{margin-left:auto;font-size:7px;letter-spacing:.2px;text-transform:none;font-weight:700;display:flex;gap:3px;align-items:center}}
/* big number + stats */
.big b{{display:block;font-size:66px;font-weight:800;letter-spacing:-3px;line-height:60px}}
.big i{{display:block;font-size:8.5px;font-weight:800;letter-spacing:1.5px;font-style:normal;margin-top:3px}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);row-gap:5px}}
.stat{{border-left:2px solid #000;padding-left:6px;height:38px;overflow:hidden}}
.stat b{{display:block;font-size:19px;font-weight:800;line-height:19px;letter-spacing:-.4px}}
.stat small{{line-height:0;font-size:8px;font-weight:700;margin-left:2px;letter-spacing:0}}
.stat i{{display:block;font-size:7px;font-weight:800;font-style:normal;text-transform:uppercase;letter-spacing:.2px;margin-top:2px;white-space:nowrap}}
.stat span{{display:block;font:700 7.5px 'Azeret Mono',monospace;white-space:nowrap}}
/* languages */
.lgs{{display:grid;grid-template-columns:1fr 1fr;column-gap:10px;row-gap:2px;margin-top:5px}}
.lg{{display:flex;align-items:center;gap:4px;font-size:9.5px;font-weight:600;white-space:nowrap}}
.lg em{{margin-left:auto;font-size:8px;font-weight:700;font-style:normal}}
/* tiles */
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}}
.tile{{background:#000;color:#fff;border-radius:5px;height:40px;display:flex;align-items:center;gap:5px;padding:0 5px;overflow:hidden}}
.tile b{{display:block;font-size:18px;font-weight:800;line-height:18px;letter-spacing:-.3px}}
.tile i{{display:block;font:700 7.5px 'Inter Tight',sans-serif;letter-spacing:.2px;text-transform:uppercase;margin-top:2px;white-space:nowrap}}
/* lists */
.repo{{display:flex;gap:6px;height:25px;margin-bottom:2px}}
.rk{{font-size:16px;font-weight:800;width:11px;line-height:20px}}
.rb{{flex:1;min-width:0}}
.rl{{display:flex;justify-content:space-between;gap:6px;align-items:baseline;white-space:nowrap}}
.rl b{{font-size:11px;font-weight:800;overflow:hidden;text-overflow:ellipsis;line-height:12px}}
.rs{{font:800 8px 'Azeret Mono',monospace;display:flex;gap:2px;align-items:center}}
.rs .ic{{margin-left:3px}}
.rd{{font-size:8px;font-weight:600;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}}
.rm{{font:700 7.5px 'Azeret Mono',monospace}}
.rbar{{height:2px;margin-top:2px;border-bottom:1px solid #000}}
.rbar div{{height:3px;background:#000}}
.act{{display:flex;align-items:center;gap:5px;height:17px;border-bottom:1px solid #000;white-space:nowrap;font-size:9.5px}}
.act:last-child{{border-bottom:0}}
.act b{{font-weight:800}}
.act .mono{{font-size:7.5px;font-weight:700;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}}
.act em{{font-size:7.5px;font-weight:700;font-style:normal}}
.pri{{display:flex;gap:6px;align-items:flex-start;height:25px;border-bottom:1px solid #000;padding-top:2px}}
.pri:last-child{{border-bottom:0}}
.pri .ic{{margin-top:1px}}
.pri div{{min-width:0;flex:1}}
.pri b{{display:block;font-size:9.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:11px}}
.pri .mono{{display:block;font-size:7.5px;font-weight:700;white-space:nowrap;margin-top:1px}}
.empty{{font:700 8px 'Azeret Mono',monospace;padding-top:6px}}
.chips{{display:flex;flex-wrap:wrap;gap:3px}}
.chip{{border:1.5px solid #000;border-radius:4px;padding:1px 5px;font:800 8px 'Azeret Mono',monospace;white-space:nowrap}}
.chip em{{font-style:normal;font-weight:700}}
</style></head><body>{PATTERNS}
<div class="hd">{avatar_tag(vm)}
 <div class="who"><h1>{e(p["name"])}</h1>
  <div class="l2"><span class="mono">@{e(p["login"])}</span><span>{e(p["bio"])}</span></div>
  <div class="meta">{"".join(f"<span>{m}</span>" for m in meta)}</div></div>
 <div class="upd"><b>GitHub</b>Updated<br>{vm["updated"]}</div>
</div>

<div class="sec big" style="left:12px;top:70px;width:212px"><b>{fmt(vm["year_total"])}</b><i>CONTRIBUTIONS IN {vm["year"]}</i></div>
<div class="sec stats" style="left:226px;top:70px;width:306px">{stats_html}</div>
<div class="sec" style="left:548px;top:68px;width:240px">
 <div class="h">{icon("code", 10)}Languages<span class="r">by repo</span></div>{lang_bar}<div class="lgs">{lang_legend}</div></div>

<div class="sec" style="left:12px;top:158px;width:398px">
 <div class="h">{icon("grid", 10)}The last 12 months<span class="r">weekly totals, peak {peak} &nbsp;Less {legend} More</span></div>{hm}</div>
<div class="sec" style="left:420px;top:158px;width:368px">
 <div class="h">{icon("repo", 10)}Profile<span class="r">all time</span></div><div class="tiles">{tiles}</div></div>

<div class="sec" style="left:12px;top:268px;width:250px">
 <div class="h">{icon("book", 10)}Top repositories<span class="r">by stars</span></div>{repos}</div>
<div class="sec" style="left:274px;top:268px;width:252px">
 <div class="h">{icon("pulse", 10)}Recent activity</div>{acts}</div>
<div class="sec" style="left:538px;top:268px;width:250px">
 <div class="h">{icon("pr", 10)}Latest pull requests</div>{prs}</div>

<div class="sec" style="left:12px;top:400px;width:180px"><div class="h">{icon("cal", 10)}By weekday<span class="r">12 months</span></div>{wd}</div>
<div class="sec" style="left:208px;top:400px;width:180px"><div class="h">{icon("clock", 10)}Hour of day<span class="r">last 100 events</span></div>{hr}</div>
<div class="sec" style="left:404px;top:400px;width:176px"><div class="h">{icon("bars", 10)}By month, {vm["year"]}</div>{mo}</div>
<div class="sec" style="left:596px;top:400px;width:192px"><div class="h">{icon("tag", 10)}Topics</div><div class="chips">{topics}</div></div>
</body></html>"""


# ---------------------------------------------------------------------- capture
def capture(html_path, png_path):
    from playwright.sync_api import sync_playwright
    from PIL import Image
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--font-render-hinting=none", "--disable-lcd-text", "--disable-font-subpixel-positioning"])
        pg = b.new_page(viewport={"width": 800, "height": 480}, device_scale_factor=1)
        pg.goto("file://" + html_path, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        raw = png_path.replace(".png", "-gray.png")
        pg.screenshot(path=raw)
        b.close()
    im = Image.open(raw).convert("L")
    im.point(lambda v: 255 if v > 150 else 0).convert("1", dither=Image.NONE).save(png_path, optimize=True)
    os.remove(raw)


def main():
    os.makedirs(OUT, exist_ok=True)
    raw = fetch_mock(LOGIN) if os.environ.get("MOCK") else fetch_real(LOGIN)
    vm = compute(raw)
    page = os.path.join(OUT, "page.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(render(vm))
    capture(page, os.path.join(OUT, "dashboard.png"))
    version = NOW.strftime("%Y%m%d%H%M")
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump({"version": version, "updated": vm["updated"], "login": LOGIN,
                   "image": "dashboard.png"}, f)
    with open(os.path.join(OUT, "index.html"), "w") as f:
        f.write(f'<!DOCTYPE html><meta charset="utf-8"><title>GitHub dashboard</title>'
                f'<body style="margin:0;background:#888;display:grid;place-items:center;min-height:100vh">'
                f'<img src="dashboard.png?v={version}" width="800" height="480" style="image-rendering:pixelated;max-width:100%;height:auto">')
    print("OK", version)


if __name__ == "__main__":
    main()
