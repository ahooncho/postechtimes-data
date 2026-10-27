#!/usr/bin/env python3
"""포항공대신문 기사 목록 보관소(data/) 만들기·갱신.

앱이 학교 서버(times.postech.ac.kr)를 사용자마다 수십 번씩 두드리지 않도록, 기사 목록과 호 구성을
이 저장소의 data/ 에 JSON으로 보관합니다. 앱은 이 파일들로 홈을 만들고, 기사 본문만 열 때 사이트에서 받습니다.

명령
    python3 tools/archive/ptimes_archive.py backfill   # 처음 한 번: 전체 기사 목록을 천천히 받아 보관
    python3 tools/archive/ptimes_archive.py update     # 새 호가 나올 때마다: 새 기사만 받아 추가
    python3 tools/archive/ptimes_archive.py build      # 네트워크 없이 data/articles.json에서 호 파일만 다시 만들기
    python3 tools/archive/ptimes_archive.py watch      # 사이트가 바뀌었을 때만 update (RSS 조건부 요청, 평소엔 304)

서버 예절
    - GET만 씁니다. HEAD는 사이트 보안 장치가 오류 페이지로 돌려보냅니다.
    - 요청 사이 1.5초 이상 쉬고(--delay), 403/429나 오류 페이지 리디렉션을 받으면 즉시 멈춥니다(방화벽 차단 방지).
    - backfill은 중간에 멈춰도 같은 명령으로 이어서 받습니다(data/.backfill_state.json).

표준 라이브러리만 씁니다(Python 3.10+).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://times.postech.ac.kr"
KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

# 영문판(The POSTECH Times) 1차 분류 코드. 사이트 메뉴: Headlines·News·Campus·Postech Microscope·Opinion·Feature·Culture
ENGLISH_SECTIONS = {"S1N28", "S1N9", "S1N10", "S1N24", "S1N13", "S1N14", "S1N30"}
HEADLINES = "S1N28"


# ─────────────────────────────── 네트워크

class Blocked(Exception):
    pass


USER_AGENT = "PostechTimesArchive/1.0 (+https://github.com/ahooncho/postechtimes-data)"
BLOCKED_HINT = "한참 뒤(30분~몇 시간) 같은 명령으로 이어서 받으세요."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """사이트 보안 장치는 거절할 때 403 대신 302로 오류 페이지(se-cu.com/ndsoft/error.html)로 보냅니다.
    리디렉션을 따라가면 오류 페이지를 '기사 0건'으로 잘못 읽으므로, 따라가지 않고 직접 판단합니다."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class Fetcher:
    """GET만 씁니다(HEAD는 사이트 보안 장치가 막습니다). 요청 사이 delay초 이상 쉽니다."""

    def __init__(self, delay: float):
        self.delay = delay
        self.last = 0.0
        self.count = 0

    def request(self, path: str, query: dict[str, str] | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(url, headers={
            "Accept-Language": "ko-KR,ko;q=0.9", "User-Agent": USER_AGENT, **(headers or {}),
        })
        try:
            with _opener.open(req, timeout=30) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, dict(e.headers), b""
            if e.code in (403, 429):
                raise Blocked(f"서버가 {e.code}로 거절했습니다. {BLOCKED_HINT}") from e
            if 300 <= e.code < 400:
                where = e.headers.get("Location", "")
                raise Blocked(f"서버가 오류 페이지로 돌려보냈습니다({e.code} → {where}). {BLOCKED_HINT}") from e
            raise
        finally:
            self.last = time.monotonic()
            self.count += 1

    def get(self, path: str, query: dict[str, str] | None = None) -> str:
        _, _, body = self.request(path, query)
        return body.decode("utf-8", errors="replace")


# ─────────────────────────────── HTML 파싱 (앱의 PageParsers.swift와 같은 규칙)

def text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace(" ", " ")
    s = re.sub(r"[ \t\r]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


DATE_RE = re.compile(r"(\d{4})[-.](\d{2})[-.](\d{2})\s+(\d{2}):(\d{2})")


def parse_date(s: str) -> str | None:
    m = DATE_RE.search(s)
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    return dt.datetime(y, mo, d, h, mi, tzinfo=KST).isoformat()


def photo_url(block: str) -> str | None:
    """썸네일 ./thumbnail/202610/24330_28609_248_v150.jpg → 원본 /news/photo/202610/24330_28609_248.jpg
    JPEG만 사진으로 씁니다. PNG 도표는 원본 .jpg가 없어 앱에서 불러오기가 실패하고 사진 자리가 지워집니다."""
    m = re.search(r"background-image:\s*url\(['\"]?([^'\")]+)['\"]?\)", block)
    if not m:
        return None
    raw = m.group(1).replace("/thumbnail/", "/photo/")
    raw = re.sub(r"_v\d+(\.[A-Za-z]+)$", r"\1", raw)
    url = urllib.parse.urljoin(BASE + "/news/", raw).replace("http://", "https://")
    return url if re.search(r"\.jpe?g$", url, re.I) else None


def parse_list(page: str) -> list[dict]:
    m = re.search(r'class="article-list-content[^"]*"(.*?)</section>', page, re.S)
    region = m.group(1) if m else page
    out, seen = [], set()
    for block in region.split('class="list-block"')[1:]:
        idm = re.search(r"articleView\.html\?idxno=(\d+)", block)
        if not idm:
            continue
        aid = int(idm.group(1))
        if aid in seen:
            continue
        tm = re.search(r'class="list-titles"[^>]*>(.*?)</div>', block, re.S) or \
            re.search(r'class="show-for-sr"[^>]*>(.*?)</span>', block, re.S)
        title = text(tm.group(1)) if tm else ""
        if not title:
            continue
        seen.add(aid)
        dm = re.search(r'class="list-dated"[^>]*>(.*?)</div>', block, re.S)
        parts = [p.strip() for p in text(dm.group(1)).split("|")] if dm else []
        parts = [p for p in parts if p]
        date_part = next((p for p in reversed(parts) if re.match(r"\d{4}[-.]", p)), None)
        rest = [p for p in parts if p != date_part]
        category = rest[0] if len(rest) >= 2 else None
        bylines = [b.strip() for r in (rest[1:] if len(rest) >= 2 else rest) for b in r.split(",") if b.strip()]
        out.append({
            "id": aid, "title": title, "published": parse_date(date_part or ""),
            "category": category, "bylines": bylines, "photo": photo_url(block),
        })
    return out


def parse_categories(page: str) -> dict[str, dict]:
    """사이트 메뉴의 분류 링크를 순서대로 읽어 이름 → {code, parent}. 2차 분류는 바로 앞 1차 분류에 속합니다."""
    cats: dict[str, dict] = {}
    current = None
    for m in re.finditer(r'<a\b[^>]*href="[^"]*sc_(sub_)?section_code=(S[12]N\d+)[^"]*"[^>]*>(.*?)</a>', page, re.S):
        code, label = m.group(2), text(m.group(3))
        if not label or len(label) >= 30:
            continue
        if code.startswith("S1N"):
            current = code
            cats.setdefault(label, {"code": code, "parent": None})
        else:
            cats.setdefault(label, {"code": code, "parent": current})
    return cats


def total_count(page: str) -> int | None:
    m = re.search(r"total=(\d+)", page)
    return int(m.group(1)) if m else None


BYLINE_RE = re.compile(r"^([가-힣]{2,4})\s*기자(?:\s|$)")


def article_body(page: str) -> str:
    """기사 본문: <div id="article-view-content-div" …> 부터 저작권 표시(view-copyright) 전까지.
    (예전 판형은 <article id=…>였으므로 그것도 받습니다.)"""
    m = re.search(r'<(?:div|article)\b[^>]*id="article-view-content-div"[^>]*>(.*?)(?:<div class="view-copyright"|</article>)', page, re.S)
    return m.group(1) if m else ""


def parse_briefs(page: str) -> list[dict]:
    """단신 묶음 본문 → 꼭지들.
    꼭지는 제목(<h1>~<h4> 또는 전체가 굵은 문단)으로 시작하고 "OOO 기자" 줄로 끝납니다.
    바이라인은 따로 한 문단이거나(오른쪽 정렬·굵게일 때도 있음) 마지막 문단 끝 <br /> 뒤에 붙습니다."""
    body = article_body(page)
    if not body:
        return []
    briefs, current = [], None

    def close():
        nonlocal current
        if current and current["paragraphs"]:
            briefs.append(current)
        current = None

    for m in re.finditer(r"<(h[1-4]|p)\b[^>]*>(.*?)</\1>", body, re.S):
        tag, raw = m.group(1), m.group(2)
        t = text(raw)
        if not t or "저작권자" in t:
            continue
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        last = BYLINE_RE.match(lines[-1]) if lines and len(lines[-1]) < 60 else None
        if last and len(lines) == 1:                 # 바이라인만 있는 문단
            if current is not None:
                current["reporter"] = f"{last.group(1)} 기자"
            close()
            continue
        bold = "".join(text(x) for x in re.findall(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", raw, re.S))
        if tag != "p" or (bold and re.sub(r"\s", "", bold) == re.sub(r"\s", "", t)):
            close()
            current = {"title": t.replace("\n", " "), "reporter": None, "paragraphs": []}
            continue
        if last:
            lines = lines[:-1]
        if current is not None and lines:
            current["paragraphs"].append("\n".join(lines))
        if last and current is not None:
            current["reporter"] = f"{last.group(1)} 기자"
            close()
    close()
    return briefs


# ─────────────────────────────── 분류·판·호

def has_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)


def classify(article: dict, cats: dict[str, dict]) -> dict:
    """목록의 분류 이름을 코드로 바꾸고 판을 정합니다."""
    a = dict(article)
    name = a.get("category")
    a["sectionCode"] = a["subSectionCode"] = a["sectionName"] = a["subSectionName"] = None
    hit = cats.get(name) if name else None
    if hit and hit["parent"]:
        a["subSectionCode"], a["subSectionName"] = hit["code"], name
        a["sectionCode"] = hit["parent"]
        a["sectionName"] = next((k for k, v in cats.items() if v["code"] == hit["parent"]), None)
    elif hit:
        a["sectionCode"], a["sectionName"] = hit["code"], name
    else:
        a["sectionName"] = name
    if a["sectionCode"]:
        a["edition"] = "en" if a["sectionCode"] in ENGLISH_SECTIONS else "ko"
    else:
        a["edition"] = "ko" if has_hangul((name or "") + a["title"]) else "en"
    return a


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def cluster(articles: list[dict], gap_days: float) -> list[list[dict]]:
    """등록 시각 순으로 늘어놓고 gap_days 넘게 벌어지면 다른 호로 나눕니다."""
    dated = sorted((a for a in articles if a.get("published")), key=lambda a: a["published"])
    groups: list[list[dict]] = []
    for a in dated:
        if groups and (parse_iso(a["published"]) - parse_iso(groups[-1][-1]["published"])).total_seconds() <= gap_days * 86400:
            groups[-1].append(a)
        else:
            groups.append([a])
    return groups


def build_issues(articles: list[dict], config: dict, overrides: dict) -> dict[str, list[dict]]:
    gap = config.get("gapDays", 3)
    excluded = set(overrides.get("excludeArticles", []))
    pool = [a for a in articles if a["id"] not in excluded]
    issues: dict[str, list[dict]] = {"ko": [], "en": []}

    # 국문 호수. 호 안 제목의 "제NNN호"를 표로 셉니다.
    #  - 형산만평·단신보도는 그 호 스스로(N, 가중치 3), 독자리뷰 "제N호 '…'를 읽고"는 지난 호를 다루므로 N+1(가중치 2),
    #    그 밖의 제목은 N(가중치 1).
    #  - 다른 연재물 번호("제17호 태풍")나 잘못 친 번호에 속지 않도록, 날짜순으로 엄격히 커지는 가장 긴 수열만 남깁니다.
    #  - 비어 있는 호는 앞뒤 호수 차이와 빈 묶음 수가 정확히 맞을 때만 차례로 채웁니다.
    ko_groups = cluster([a for a in pool if a["edition"] == "ko"], gap)
    numbered = []
    for g in ko_groups:
        votes = collections.Counter()
        for a in g:
            m = re.search(r"제\s*(\d+)\s*호", a["title"])
            if not m:
                continue
            n = int(m.group(1))
            if "읽고" in a["title"] or a.get("category") == "독자리뷰":
                votes[n + 1] += 2          # 독자리뷰 "제N호 '…'를 읽고"는 다음 호(N+1)에 실림
            elif a.get("category") in ("형산만평", "단신보도") or re.search(r"(형산만평|단신보도)", a["title"]):
                votes[n] += 3              # 그 호 스스로를 가리키는 꼭지
            else:
                votes[n] += 1
        numbered.append(votes.most_common(1)[0][0] if votes else None)
    # 날짜순으로 엄격히 커지는 가장 긴 수열만 남깁니다(최장 증가 부분 수열). 어긋난 값은 다른 연재물·지난 호 언급.
    known = [(i, n) for i, n in enumerate(numbered) if n is not None]
    best, prev = [1] * len(known), [-1] * len(known)
    for x in range(len(known)):
        for y in range(x):
            if known[y][1] < known[x][1] and best[y] + 1 > best[x]:
                best[x], prev[x] = best[y] + 1, y
    keep, x = set(), (max(range(len(known)), key=lambda k: best[k]) if known else -1)
    while x != -1:
        keep.add(known[x][0]); x = prev[x]
    numbered = [n if i in keep else None for i, n in enumerate(numbered)]
    # 빈 호 채우기: 앞 p, 뒤 q 사이 빈 묶음이 정확히 q-p-1개면 차례로
    known = [i for i, n in enumerate(numbered) if n is not None]
    for a_, b_ in zip(known, known[1:]):
        gaps = [k for k in range(a_ + 1, b_) if len(ko_groups[k]) >= 5]
        if gaps and numbered[b_] - numbered[a_] - 1 == len(gaps):
            for off, k in enumerate(gaps, start=1):
                numbered[k] = numbered[a_] + off
    for g, n in zip(ko_groups, numbered):
        if n is not None:
            issues["ko"].append({"number": n, "articles": g})

    # 영문: CMS에 호수가 없으므로 Headlines가 있는 묶음만 호로 보고, 기준점 하나에서 앞뒤로 셉니다.
    en_groups = [g for g in cluster([a for a in pool if a["edition"] == "en"], gap)
                 if any(a.get("sectionCode") == HEADLINES for a in g)]
    anchor = config["englishAnchor"]
    anchor_day = dt.date.fromisoformat(anchor["date"])
    idx = next((i for i, g in enumerate(en_groups)
                if any(parse_iso(a["published"]).date() == anchor_day for a in g)), None)
    if idx is not None:
        for i, g in enumerate(en_groups):
            issues["en"].append({"number": anchor["number"] + (i - idx), "articles": g})
    else:
        print("경고: 영문 기준점 날짜의 호를 찾지 못해 영문 호수를 매기지 못했습니다.", file=sys.stderr)

    # 사람이 고친 호수(overrides.json의 renumber: {"ko:2024-03-06": 455})
    for edition, items in issues.items():
        for it in items:
            key = f"{edition}:{it['articles'][0]['published'][:10]}"
            if key in overrides.get("renumber", {}):
                it["number"] = overrides["renumber"][key]
    return issues


# ─────────────────────────────── 파일

def load_json(path: Path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", "utf-8")
    tmp.replace(path)


def write_issue_files(articles: list[dict], briefs: dict[str, list], config: dict, overrides: dict) -> None:
    issues = build_issues(articles, config, overrides)
    index = {}
    for edition, items in issues.items():
        entries = []
        for it in sorted(items, key=lambda x: -x["number"]):
            group = sorted(it["articles"], key=lambda a: -a["id"])
            payload = {
                "edition": edition,
                "number": it["number"],
                "published": min(a["published"] for a in group),
                "lastUpload": max(a["published"] for a in group),
                "articles": [{k: a[k] for k in ("id", "title", "published", "category", "bylines", "photo",
                                                "sectionCode", "sectionName", "subSectionCode", "subSectionName")}
                             for a in group],
                "briefs": {str(a["id"]): briefs[str(a["id"])] for a in group if str(a["id"]) in briefs},
            }
            save_json(DATA / "issues" / edition / f"{it['number']}.json", payload)
            entries.append({"number": it["number"], "published": payload["published"],
                            "lastUpload": payload["lastUpload"], "count": len(group)})
        index[edition] = entries
        # 호수가 바뀌어 더는 없는 호의 파일은 지웁니다(색인과 파일이 늘 일치하도록).
        current = {f"{e['number']}.json" for e in entries}
        for stale in (DATA / "issues" / edition).glob("*.json"):
            if stale.name not in current:
                stale.unlink()
    save_json(DATA / "issues" / "index.json", index)
    print(f"호 파일: 국문 {len(index['ko'])}개, 영문 {len(index['en'])}개")


def brief_candidates(articles: list[dict]) -> list[dict]:
    return [a for a in articles if a["edition"] == "ko" and "단신" in a["title"]]


def fetch_briefs(fetcher: Fetcher, articles: list[dict], briefs: dict[str, list]) -> None:
    for a in brief_candidates(articles):
        if str(a["id"]) in briefs:
            continue
        page = fetcher.get("/news/articleView.html", {"idxno": str(a["id"])})
        briefs[str(a["id"])] = parse_briefs(page)
        save_json(DATA / "briefs.json", briefs)
        print(f"  단신 {a['id']}: 꼭지 {len(briefs[str(a['id'])])}개")


# ─────────────────────────────── 명령

def cmd_backfill(args) -> None:
    fetcher = Fetcher(args.delay)
    state = load_json(DATA / ".backfill_state.json", {"next_page": 1, "done": False})
    raw = {a["id"]: a for a in load_json(DATA / ".raw_articles.json", [])}
    cats = load_json(DATA / "categories.json", {})
    try:
        while not state["done"]:
            page_no = state["next_page"]
            page = fetcher.get("/news/articleList.html", {"view_type": "sm", "page": str(page_no)})
            if not cats:
                cats = parse_categories(page)
                save_json(DATA / "categories.json", cats)
            items = parse_list(page)
            for it in items:
                raw[it["id"]] = it
            total = total_count(page)
            print(f"목록 {page_no}쪽: {len(items)}건 (누적 {len(raw)}/{total or '?'})")
            state["next_page"] = page_no + 1
            if len(items) < 20:
                state["done"] = True
            save_json(DATA / ".raw_articles.json", sorted(raw.values(), key=lambda a: -a["id"]))
            save_json(DATA / ".backfill_state.json", state)
    except Blocked as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    finish(fetcher, list(raw.values()), cats, fetch_bodies=True)


def cmd_update(args) -> None:
    fetcher = Fetcher(args.delay)
    known = {a["id"]: a for a in load_json(DATA / "articles.json", [])}
    cats = load_json(DATA / "categories.json", {})
    new = []
    try:
        for page_no in range(1, args.max_pages + 1):
            page = fetcher.get("/news/articleList.html", {"view_type": "sm", "page": str(page_no)})
            if page_no == 1:
                cats = {**cats, **parse_categories(page)}
                save_json(DATA / "categories.json", cats)
            items = parse_list(page)
            fresh = [it for it in items if it["id"] not in known]
            new += fresh
            print(f"목록 {page_no}쪽: 새 기사 {len(fresh)}건")
            if len(fresh) < len(items) or len(items) < 20:
                break     # 이미 아는 기사가 나오면 그 뒤는 다 아는 기사
    except Blocked as e:
        if getattr(args, "from_watch", False):
            raise
        print(e, file=sys.stderr)
        sys.exit(2)
    for it in new:
        known[it["id"]] = it
    finish(fetcher, list(known.values()), cats, fetch_bodies=True)
    print(f"새 기사 {len(new)}건 추가. data/ 의 변경을 확인하고 커밋하세요.")


def rss_changed(fetcher: Fetcher, state: dict) -> bool:
    """RSS 전체기사 피드는 정적 파일이라 ETag·Last-Modified가 붙습니다. 지난번 값을 함께 보내면
    바뀌지 않았을 때 서버가 본문 없이 304만 돌려줍니다(요청 하나에 수백 바이트).
    피드 내용은 신호로만 씁니다 — 호를 올리는 날은 피드 창(최근 수십 건)을 넘길 만큼 기사가 올라옵니다."""
    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("lastModified"):
        headers["If-Modified-Since"] = state["lastModified"]
    status, res_headers, _ = fetcher.request("/rss/allArticle.xml", headers=headers)
    if status == 304:
        return False
    changed = (res_headers.get("ETag"), res_headers.get("Last-Modified")) != (state.get("etag"), state.get("lastModified"))
    state["etag"], state["lastModified"] = res_headers.get("ETag"), res_headers.get("Last-Modified")
    return changed


def cmd_watch(args) -> None:
    """사이트가 바뀌었을 때만 update를 돌립니다. --once는 한 번만 확인(GitHub Actions 예약 작업용).
    차단되면 그 시각을 상태 파일에 적어 두고, 한 시간 동안은 확인하지 않습니다(예약 작업이 15분마다 돌아도)."""
    fetcher = Fetcher(args.delay)
    state_path = DATA / ".watch_state.json"
    state = load_json(state_path, {})
    while True:
        now = dt.datetime.now(KST)
        stamp = now.strftime("%m-%d %H:%M")
        blocked_until = state.get("blockedUntil")
        if blocked_until and now < dt.datetime.fromisoformat(blocked_until):
            print(f"[{stamp}] 차단 대기 중({blocked_until}까지) — 요청하지 않음")
        else:
            previous = {k: state.get(k) for k in ("etag", "lastModified")}
            try:
                if rss_changed(fetcher, state):
                    print(f"[{stamp}] 사이트가 바뀌었습니다(Last-Modified {state.get('lastModified')}). 새 기사를 확인합니다.")
                    cmd_update(argparse.Namespace(delay=args.delay, max_pages=args.max_pages, from_watch=True))
                else:
                    print(f"[{stamp}] 변경 없음(304)")
                state.pop("blockedUntil", None)
            except Blocked as e:
                print(f"[{stamp}] {e}", file=sys.stderr)
                state.update(previous)      # 갱신을 못 했으니 다음 확인에서 다시 '바뀜'으로 보이게
                state["blockedUntil"] = (now + dt.timedelta(hours=1)).isoformat()
            save_json(state_path, state)
        if args.once:
            return
        time.sleep(args.interval * 60)


def cmd_inspect(args) -> None:
    """기사 페이지를 받아 본문 구조를 요약합니다(파서 점검용, 기사마다 요청 1회)."""
    fetcher = Fetcher(args.delay)
    for idxno in args.id:
        status, headers, body = fetcher.request("/news/articleView.html", {"idxno": str(idxno)})
        page = body.decode("utf-8", errors="replace")
        print(f"=== {idxno}: status {status} · {len(page)} chars")
        content = article_body(page)
        print(f"--- 본문 {len(content)}자 ---")
        print(re.sub(r"\s+", " ", content[:2500]))
        briefs = parse_briefs(page)
        print(f"--- 꼭지 {len(briefs)}개 ---")
        for b in briefs:
            print(" ", b["title"], "|", b["reporter"], "|", len(b["paragraphs"]))


def cmd_build(args) -> None:
    finish(None, load_json(DATA / "articles.json", []), load_json(DATA / "categories.json", {}), fetch_bodies=False)


def finish(fetcher: Fetcher | None, raw: list[dict], cats: dict, fetch_bodies: bool) -> None:
    config = load_json(DATA / "config.json", {"gapDays": 3, "englishAnchor": {"number": 199, "date": "2026-10-07"}})
    save_json(DATA / "config.json", config)
    overrides = load_json(DATA / "overrides.json", {"excludeArticles": [], "renumber": {}})
    save_json(DATA / "overrides.json", overrides)
    articles = sorted((classify(a, cats) for a in raw), key=lambda a: -a["id"])
    save_json(DATA / "articles.json", articles)
    briefs = load_json(DATA / "briefs.json", {})
    if fetch_bodies and fetcher:
        try:
            fetch_briefs(fetcher, articles, briefs)
        except Blocked as e:
            print(e, file=sys.stderr)
    write_issue_files(articles, briefs, config, overrides)
    if fetcher:
        print(f"요청 {fetcher.count}회")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="전체 기사 목록 받기(처음 한 번, 이어받기 가능)")
    b.add_argument("--delay", type=float, default=1.5)
    u = sub.add_parser("update", help="새 기사만 받아 추가(새 호가 나올 때마다)")
    u.add_argument("--delay", type=float, default=1.5)
    u.add_argument("--max-pages", type=int, default=10)
    sub.add_parser("build", help="네트워크 없이 호 파일 다시 만들기")
    w = sub.add_parser("watch", help="사이트가 바뀌었을 때만 update(RSS 조건부 요청으로 확인)")
    w.add_argument("--interval", type=float, default=10, help="확인 간격(분)")
    w.add_argument("--once", action="store_true", help="한 번만 확인하고 끝내기")
    w.add_argument("--delay", type=float, default=1.5)
    w.add_argument("--max-pages", type=int, default=10)
    i = sub.add_parser("inspect", help="기사 페이지 하나의 본문 구조 보기(파서 점검)")
    i.add_argument("id", type=int, nargs="+")
    i.add_argument("--delay", type=float, default=1.5)
    args = p.parse_args()
    {"backfill": cmd_backfill, "update": cmd_update, "build": cmd_build, "watch": cmd_watch,
     "inspect": cmd_inspect}[args.cmd](args)


if __name__ == "__main__":
    main()
