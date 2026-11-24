#!/usr/bin/env python3
"""Check a rules HTML fragment against HTML-SPEC.md and against its source PDF.

Usage: check.py rules/html/<id>.html [--verbose] [--json]

Exit code 0 = no errors (warnings allowed), 1 = errors.
"""
import difflib
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.dirname(HERE)

VOID = {"br", "img", "hr", "wbr", "col"}
ALLOWED_TAGS = {"section", "h2", "h3", "h4", "h5", "h6", "p", "ol", "ul", "li", "table", "thead", "tbody",
                "tfoot", "tr", "th", "td", "caption", "colgroup", "col", "figure", "figcaption", "strong", "b", "em", "i",
                "u", "s", "sup", "sub", "a", "mark", "br", "img", "div", "span", "footer", "blockquote", "hr",
                "dl", "dt", "dd", "del", "ins"}
ALLOWED_CLASSES = {"wsdc-rules", "rules-meta", "rules-footer", "rules-chart", "rules-table", "rules-legend",
                   "rules-letterhead", "green", "rules-note", "rules-draft-note", "rules-level-bar", "rules-arrow"}
BLOCK = {"p", "ol", "ul", "li", "table", "tr", "td", "th", "div", "section", "h2", "h3", "h4", "h5", "h6",
         "figure", "figcaption", "footer", "blockquote", "dl", "dt", "dd"}
ALLOWED_ATTRS = {"id", "class", "href", "src", "alt", "colspan", "rowspan", "data-list-text", "data-version",
                 "data-document", "lang", "title", "start", "type", "width", "height", "scope", "target", "rel",
                 "data-note"}


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.warnings = []
        self.ids = []
        self.hrefs = []
        self.words = []          # (word, mark_color or None)
        self.roots = []
        self.depth0_text = ""
        self.buf = []            # (char, color) stream; block boundaries add a space

    def pos(self):
        return "line %d" % self.getpos()[0]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ALLOWED_TAGS:
            self.errors.append("%s: tag <%s> not allowed" % (self.pos(), tag))
        for k in a:
            if k not in ALLOWED_ATTRS:
                self.errors.append("%s: attribute %s on <%s> not allowed" % (self.pos(), k, tag))
        for c in (a.get("class") or "").split():
            if c not in ALLOWED_CLASSES:
                self.errors.append("%s: class '%s' not allowed" % (self.pos(), c))
        if "id" in a:
            self.ids.append((a["id"], self.pos()))
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-]*", a["id"] or ""):
                self.errors.append("%s: invalid id '%s'" % (self.pos(), a["id"]))
        if tag == "a" and a.get("href", "").startswith("#"):
            self.hrefs.append((a["href"][1:], self.pos()))
        if tag == "img":
            src = a.get("src", "")
            if not src.startswith("../img/"):
                self.errors.append("%s: img src must start with ../img/ (%s)" % (self.pos(), src))
            elif not os.path.exists(os.path.join(RULES, src[3:])):
                self.errors.append("%s: image file missing: %s" % (self.pos(), src))
        if not self.stack:
            self.roots.append((tag, a))
        if tag in BLOCK and any(t == "mark" for t, _ in self.stack):
            self.errors.append("%s: <mark> contains block element <%s>" % (self.pos(), tag))
        if tag in BLOCK or tag in ("br", "td", "th"):
            self.buf.append((" ", None))
        if tag == "li" and a.get("data-list-text"):
            color = None
            for t, aa in self.stack:
                if t == "mark":
                    color = "G" if "green" in (aa.get("class") or "") else "Y"
            self.buf.append((" ", None))
            for ch in a["data-list-text"]:
                self.buf.append((ch, color))
            self.buf.append((" ", None))
        if tag not in VOID:
            self.stack.append((tag, a))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self.buf.append((" ", None))
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append("%s: stray </%s>" % (self.pos(), tag))
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        # mismatch
        names = [t for t, _ in self.stack]
        if tag in names:
            while self.stack and self.stack[-1][0] != tag:
                t, _ = self.stack.pop()
                self.errors.append("%s: <%s> not closed before </%s>" % (self.pos(), t, tag))
            self.stack.pop()
        else:
            self.errors.append("%s: stray </%s>" % (self.pos(), tag))

    def handle_data(self, data):
        if not self.stack:
            if data.strip():
                self.errors.append("%s: text outside root element" % self.pos())
            return
        color = None
        for t, a in self.stack:
            if t == "mark":
                color = "G" if "green" in (a.get("class") or "") else "Y"
        for ch in data:
            self.buf.append((ch, color))

    def finish_words(self):
        self.words = []
        cur, col = "", None
        for ch, c in self.buf + [(" ", None)]:
            if ch.isspace():
                if cur:
                    self.words.append((cur, col))
                cur, col = "", None
            else:
                cur += ch
                col = col or c
        return self.words


def norm(w):
    w = w.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    w = w.lower()
    w = re.sub(r"[^\w$%&@#+/-]", "", w)
    w = w.strip("-/")
    return w


def norm_seq(words):
    out = []
    for w in words:
        n = norm(w)
        if n:
            # split on slashes/hyphens so "Leader/Follower" == "Leader / Follower"
            for part in re.split(r"[-/]", n):
                if not part:
                    continue
                m = re.fullmatch(r"([a-z]{2,})(\d{1,2})", part)  # footnote digit glued to a word: "board1"
                if m:
                    out.extend([m.group(1), m.group(2)])
                else:
                    out.append(part)
    return out


def pdf_words(pdf_path, pages_spec, ignore_lines):
    doc = pymupdf.open(pdf_path)
    pages = list(range(len(doc)))
    if pages_spec:
        a, b = pages_spec.split("-")
        pages = list(range(int(a) - 1, int(b)))
    # collect lines with their marks
    page_lines = []
    for pno in pages:
        page = doc[pno]
        rects = []
        for dr in page.get_drawings():
            f = dr.get("fill")
            if f and len(f) == 3:
                if f[0] > 0.85 and f[1] > 0.85 and f[2] < 0.35:
                    rects.append((pymupdf.Rect(dr["rect"]), "Y"))
                elif f[0] < 0.2 and f[1] > 0.85 and f[2] < 0.35:
                    rects.append((pymupdf.Rect(dr["rect"]), "G"))
        words = page.get_text("words", sort=True)
        lines = {}
        order = []
        for w in words:
            k = (w[5], w[6])
            if k not in lines:
                lines[k] = []
                order.append(k)
            lines[k].append(w)
        order.sort(key=lambda k: (round(lines[k][0][1] / 3), lines[k][0][0]))
        out = []
        ph = page.rect.height
        for k in order:
            ws = sorted(lines[k], key=lambda w: w[0])
            ymid = (ws[0][1] + ws[0][3]) / 2 / ph
            toks = []
            for w in ws:
                r = pymupdf.Rect(w[:4])
                c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                col = None
                for rr, cc in rects:
                    if rr.contains(c):
                        col = cc
                        break
                for part in w[4].split("\u200b\u200b"):   # Google Docs export: double ZWSP = space
                    part = part.replace("\u200b", "")
                    if part:
                        toks.append((part, col))
            out.append((toks, ymid < 0.09 or ymid > 0.91))
        page_lines.append(out)
    # lines repeated on 3+ pages = running headers/footers
    cnt = Counter()
    for pl in page_lines:
        for l in set(" ".join(t for t, _ in toks) for toks, margin in pl if margin):
            cnt[re.sub(r"\d+", "#", l)] += 1
    rep = {k for k, v in cnt.items() if v >= 3 and len(page_lines) >= 3}
    result = []
    for pl in page_lines:
        for toks, margin in pl:
            line = " ".join(t for t, _ in toks)
            key = re.sub(r"\d+", "#", line)
            if not toks:
                continue
            if margin and key in rep:
                continue
            if re.search(r"\.{6,}", line):  # table of contents
                continue
            if re.fullmatch(r"\s*table of contents\s*", line, re.I):
                continue
            if re.fullmatch(r"(page )?\d+( of \d+)?( pages)?", line.strip().lower()):
                continue
            if any(re.search(p, line) for p in ignore_lines):
                continue
            toks = [t for t in toks if "web.archive.org" not in t[0] and not re.match(r"^\(?https?://", t[0])]
            if toks:
                result.append(toks)
    # de-hyphenate at line ends
    flat = []
    for i, toks in enumerate(result):
        for j, (t, c) in enumerate(toks):
            flat.append([t, c])
        if flat and flat[-1][0].endswith("-") and len(flat[-1][0]) > 1 and i + 1 < len(result) and result[i + 1] \
                and not re.search(r"[/.@-]", flat[-1][0][:-1]):
            nxt = result[i + 1][0][0]
            if nxt[:1].islower():
                flat[-1][0] = flat[-1][0][:-1] + "\u0000"  # join marker
    joined = []
    for t, c in flat:
        if joined and joined[-1][0].endswith("\u0000"):
            joined[-1][0] = joined[-1][0][:-1] + t
            joined[-1][1] = joined[-1][1] or c
        else:
            joined.append([t, c])
    return joined


def ngram_cover(a, b, n=3):
    """Order-insensitive coverage: a word of `a` counts as covered when one of
    the n-grams (n, then n-1 … 2) that contain it also occurs in `b`.
    Returns (covered_count, list of uncovered runs as strings)."""
    covered = [False] * len(a)
    for k in range(n, 1, -1):
        grams = set(tuple(b[i:i + k]) for i in range(len(b) - k + 1))
        for i in range(len(a) - k + 1):
            if tuple(a[i:i + k]) in grams:
                for j in range(i, i + k):
                    covered[j] = True
    # single words only count if the text is very short
    if len(a) < 3:
        bs = set(b)
        for i, w in enumerate(a):
            covered[i] = covered[i] or w in bs
    runs, cur = [], []
    for w, c in zip(a, covered):
        if not c:
            cur.append(w)
        elif cur:
            runs.append(" ".join(cur))
            cur = []
    if cur:
        runs.append(" ".join(cur))
    return sum(covered), runs


def bag_cover(a, b):
    ca, cb = Counter(a), Counter(b)
    common = sum(min(ca[k], cb[k]) for k in ca)
    miss = sorted((ca - cb).items(), key=lambda x: -x[1])
    extra = sorted((cb - ca).items(), key=lambda x: -x[1])
    return common, ["%s×%d" % kv for kv in miss], ["%s×%d" % kv for kv in extra]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    as_json = "--json" in sys.argv
    if len(args) != 1:
        sys.exit("usage: check.py rules/html/<id>.html [--verbose] [--json]")
    path = args[0]
    vid = os.path.basename(path)[:-5]
    versions = json.load(open(os.path.join(RULES, "versions.json")))["versions"]
    entry = next((v for v in versions if v["id"] == vid), None)
    src = open(path, encoding="utf-8").read()
    p = Parser()
    p.feed(src)
    p.close()
    p.finish_words()
    errors, warnings = list(p.errors), list(p.warnings)
    for t, _ in p.stack:
        errors.append("unclosed <%s> at end of file" % t)
    if len(p.roots) != 1 or p.roots[0][0] != "section" or "wsdc-rules" not in (p.roots[0][1].get("class") or ""):
        errors.append("file must have exactly one root <section class=\"wsdc-rules\">")
    else:
        ra = p.roots[0][1]
        if entry and ra.get("data-version") != vid:
            errors.append("root data-version='%s' should be '%s'" % (ra.get("data-version"), vid))
        if entry and ra.get("data-document") != entry["document"]:
            errors.append("root data-document='%s' should be '%s'" % (ra.get("data-document"), entry["document"]))
    idc = Counter(i for i, _ in p.ids)
    for i, n in idc.items():
        if n > 1:
            errors.append("duplicate id '%s' (%dx)" % (i, n))
    idset = set(idc)
    for h, pos in p.hrefs:
        if h not in idset:
            errors.append("%s: broken anchor #%s" % (pos, h))
    if "<footer" not in src:
        warnings.append("no <footer class=\"rules-footer\">")
    if not entry:
        errors.append("no entry '%s' in versions.json" % vid)
    report = {"file": path, "errors": errors, "warnings": warnings}
    if entry and entry.get("pdf"):
        ignore = entry.get("check_ignore_lines", [])
        pw = pdf_words(os.path.join(RULES, entry["pdf"]), entry.get("pdf_pages"), ignore)
        a = norm_seq([t for t, _ in pw])
        b = norm_seq([w for w, _ in p.words])
        cov_a, missing = ngram_cover(a, b)
        cov_b, extra = ngram_cover(b, a)
        cov = cov_a / max(1, len(a))
        prec = cov_b / max(1, len(b))
        report["coverage"] = round(cov * 100, 2)
        report["precision"] = round(prec * 100, 2)
        big_missing = [m for m in missing if len(m.split()) >= 6]
        mid_missing = [m for m in missing if 3 <= len(m.split()) < 6]
        if mid_missing:
            warnings.append("short runs missing (often table cell order, verify visually): %s" % "; ".join(mid_missing[:8]))
        big_extra = [m for m in extra if len(m.split()) >= 3]
        report["missing"] = missing
        report["extra"] = extra
        min_cov = entry.get("check_min_coverage", 0.994)
        if cov < min_cov or len(big_missing) > entry.get("check_max_long_runs", 0):
            errors.append("text coverage %.2f%% (%d PDF words missing in HTML, %d runs of 6+ words)" % (
                cov * 100, len(a) - cov_a, len(big_missing)))
        if prec < 0.995 or big_extra:
            warnings.append("precision %.2f%% (%d HTML words not in PDF, %d runs of 3+ words)" % (
                prec * 100, len(b) - cov_b, len(big_extra)))
        for color, name in (("Y", "yellow"), ("G", "green")):
            label = re.compile(r"^\(?([0-9]{1,2}|[a-z]|[ivx]{1,4})[.)]$")
            pm = norm_seq([t for t, c in pw if c == color and not label.match(t)])
            hm = norm_seq([w for w, c in p.words if c == color and not label.match(w)])
            if not pm and not hm:
                continue
            m2, mi2, ex2 = bag_cover(pm, hm)
            r = m2 / max(1, len(pm))
            r2 = m2 / max(1, len(hm))
            report["marks_" + name] = {"pdf_words": len(pm), "html_words": len(hm), "recall": round(r * 100, 1),
                                       "precision": round(r2 * 100, 1), "missing": mi2, "extra": ex2}
            if r < 0.98 or r2 < 0.98:
                errors.append("%s highlights: %d PDF words, %d HTML words, recall %.1f%%, precision %.1f%%" % (
                    name, len(pm), len(hm), r * 100, r2 * 100))
    if as_json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print("== %s" % path)
        if "coverage" in report:
            print("coverage %.2f%%  precision %.2f%%" % (report["coverage"], report["precision"]))
        for k in ("marks_yellow", "marks_green"):
            if k in report:
                m = report[k]
                print("%s: pdf %d / html %d words, recall %.1f%%, precision %.1f%%" % (
                    k, m["pdf_words"], m["html_words"], m["recall"], m["precision"]))
        for e in errors:
            print("ERROR  " + e)
        for w in warnings:
            print("warn   " + w)
        if verbose or errors:
            lim = None if verbose else 25
            if report.get("missing"):
                print("-- in PDF, not in HTML:")
                for m in report["missing"][:lim]:
                    print("   - " + m[:200])
            if report.get("extra"):
                print("-- in HTML, not in PDF:")
                for m in report["extra"][:lim]:
                    print("   + " + m[:200])
            for k in ("marks_yellow", "marks_green"):
                if k in report and (report[k]["missing"] or report[k]["extra"]):
                    print("-- %s missing: %s" % (k, report[k]["missing"][:lim]))
                    print("-- %s extra:   %s" % (k, report[k]["extra"][:lim]))
        print("OK" if not errors else "FAILED (%d errors)" % len(errors))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
