#!/usr/bin/env python3
"""Build derived data for the WSDC rules archive.

Usage: python3 rules/tools/build.py [--today YYYY-MM-DD]

Reads   rules/versions.json and rules/html/*.html
Writes  rules/versions.json            (fills sha256, bytes, pages, predecessor, current)
        rules/data/units/<id>.json     every heading, rule, paragraph and table of a version
        rules/data/changes/<id>.json   differences to the predecessor version(s)
        rules/data/mapping.json        unit-to-unit mapping between consecutive versions
        rules/data/lineages.json       one entry per rule followed through all versions
        rules/data/stats.json          size and change figures per version and section
        rules/data/search.json         compact text of all versions for full-text search
        rules/data/mapping-review.md   uncertain mappings for a human to check
        registry_event_rules.html      copy of the version currently in force
"""
import datetime
import difflib
import hashlib
import json
import os
import re
import sys

import lxml.html
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.dirname(HERE)
REPO = os.path.dirname(RULES)
DATA = os.path.join(RULES, "data")

BLOCK_SKIP = {"ol", "ul", "table", "figure"}


# ----------------------------------------------------------------- helpers

def words(text):
    return re.findall(r"[\w$%’'+-]+", text.lower())


def sim(a, b):
    if a == b:
        return 1.0
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def quick_sim(a_set, b_set):
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def clean(s):
    return re.sub(r"\s+", " ", s).strip()


def own_text(el):
    """Text of an element without nested lists/tables (those are units of their own)."""
    parts = []

    def walk(e):
        if e.tag in BLOCK_SKIP and e is not el:
            if e.tail:
                parts.append(e.tail)
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
        if e is not el and e.tail:
            parts.append(e.tail)

    walk(el)
    return clean(" ".join(parts))


def marked_text(el, nested=False):
    out = {"Y": [], "G": []}
    for m in el.iter("mark"):
        if not nested:
            # skip marks inside nested lists
            p = m.getparent()
            inside = False
            while p is not None and p is not el:
                if p.tag in BLOCK_SKIP:
                    inside = True
                    break
                p = p.getparent()
            if inside:
                continue
        col = "G" if "green" in (m.get("class") or "") else "Y"
        out[col].append(clean(m.text_content()))
    return {k: v for k, v in out.items() if v}


def table_text(tbl):
    rows = []
    for tr in tbl.iter("tr"):
        cells = [clean(c.text_content()) for c in tr if c.tag in ("td", "th")]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


# ----------------------------------------------------------------- units

def extract_units(path):
    src = open(path, encoding="utf-8").read()
    root = lxml.html.fragment_fromstring(src)
    units = []
    path_stack = []  # (level, id, title)
    auto = {}

    def cur_path():
        return [p[1] for p in path_stack]

    def anchor():
        return path_stack[-1][1] if path_stack else "top"

    def autokey(kind):
        a = anchor()
        n = auto.get((a, kind), 0) + 1
        auto[(a, kind)] = n
        return "%s~%s%d" % (a, kind, n)

    def visit(el, in_list=False):
        tag = el.tag if isinstance(el.tag, str) else None
        if tag is None:
            return
        cls = el.get("class") or ""
        if tag == "h2":
            units.append({"k": "title", "t": "meta", "path": [], "text": clean(el.text_content())})
            return
        if tag in ("h3", "h4", "h5", "h6"):
            lvl = int(tag[1])
            while path_stack and path_stack[-1][0] >= lvl:
                path_stack.pop()
            key = el.get("id") or autokey("h")
            title = clean(el.text_content())
            units.append({"k": key, "t": "heading", "lvl": lvl, "path": cur_path(), "text": title,
                          "marks": marked_text(el)})
            path_stack.append((lvl, key, title))
            return
        if tag == "div" and "rules-meta" in cls or tag == "footer":
            units.append({"k": "meta" if tag == "div" else "footer", "t": "meta", "path": [],
                          "text": clean(el.text_content()), "marks": marked_text(el, True)})
            return
        if tag == "p" and "rules-letterhead" in cls:
            return
        if tag == "figure" or tag == "table":
            tbl = el if tag == "table" else el.find(".//table")
            cap = el.find(".//figcaption")
            key = el.get("id") or (tbl.get("id") if tbl is not None else None) or autokey("t")
            text = (clean(cap.text_content()) + "\n" if cap is not None else "") + (
                table_text(tbl) if tbl is not None else clean(el.text_content()))
            units.append({"k": key, "t": "table", "path": cur_path(), "text": text, "marks": marked_text(el, True)})
            return
        if tag == "li":
            key = el.get("id") or autokey("i")
            t = "note" if el.getparent() is not None and "rules-legend" in (el.getparent().get("class") or "") else "item"
            units.append({"k": key, "t": t, "path": cur_path(), "label": el.get("data-list-text"),
                          "text": own_text(el), "marks": marked_text(el)})
            for c in el:
                if c.tag in BLOCK_SKIP or c.tag in ("p", "div"):
                    visit(c, True)
            return
        if tag == "p":
            txt = clean(el.text_content())
            if txt:
                units.append({"k": el.get("id") or autokey("p"), "t": "para", "path": cur_path(), "text": txt,
                              "marks": marked_text(el)})
            return
        for c in el:
            visit(c, in_list)

    for c in root:
        visit(c)
    for u in units:
        if not u.get("marks"):
            u.pop("marks", None)
        if u.get("label") is None:
            u.pop("label", None)
    return units


# ----------------------------------------------------------------- matching

KIND_CLASS = {"heading": "h", "item": "x", "para": "x", "note": "n", "table": "t", "meta": "m"}


def match_units(old, new):
    """Return list of (old_index or None, new_index or None, similarity, confidence)."""
    pairs = {}
    used_old = set()
    old_by_key = {}
    for i, u in enumerate(old):
        old_by_key.setdefault(u["k"], i)
    # 1. same key
    for j, u in enumerate(new):
        if "~" in u["k"]:
            continue
        i = old_by_key.get(u["k"])
        if i is None or i in used_old:
            continue
        if KIND_CLASS[old[i]["t"]] != KIND_CLASS[u["t"]]:
            continue
        s = sim(old[i]["text"], u["text"])
        if s >= 0.35 or u["t"] == "heading" and s >= 0.2:
            pairs[j] = (i, s)
            used_old.add(i)
    # 2. similarity for the rest
    old_sets = [set(words(u["text"])) for u in old]
    new_sets = [set(words(u["text"])) for u in new]
    cands = []
    for j, u in enumerate(new):
        if j in pairs:
            continue
        for i, o in enumerate(old):
            if i in used_old or KIND_CLASS[o["t"]] != KIND_CLASS[u["t"]]:
                continue
            q = quick_sim(old_sets[i], new_sets[j])
            if q < 0.3:
                continue
            s = sim(o["text"], u["text"])
            if s >= 0.55:
                # prefer candidates under the same parent section
                bonus = 0.02 if o["path"][:1] == u["path"][:1] else 0
                cands.append((s + bonus, s, i, j))
    cands.sort(reverse=True)
    for _, s, i, j in cands:
        if i in used_old or j in pairs:
            continue
        pairs[j] = (i, s)
        used_old.add(i)
    out = []
    for j, u in enumerate(new):
        if j in pairs:
            i, s = pairs[j]
            same_key = old[i]["k"] == u["k"]
            if s >= 0.999:
                conf = "high"
            elif same_key and s >= 0.6:
                conf = "high"
            elif s >= 0.8:
                conf = "medium"
            else:
                conf = "low"
            out.append((i, j, round(s, 3), conf))
        else:
            out.append((None, j, 0.0, None))
    for i in range(len(old)):
        if i not in used_old:
            out.append((i, None, 0.0, None))
    return out


def word_diff(a, b):
    """Compact word diff as list of [op, text] with op in '=', '-', '+'."""
    wa, wb = a.split(), b.split()
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(["=", " ".join(wa[i1:i2])])
        else:
            if i2 > i1:
                out.append(["-", " ".join(wa[i1:i2])])
            if j2 > j1:
                out.append(["+", " ".join(wb[j1:j2])])
    return out


# ----------------------------------------------------------------- versions

def date_key(v):
    return (v.get("effective") or "9999", v.get("published") or "")


def compute_predecessors(versions):
    by_id = {v["id"]: v for v in versions}
    usable = [v for v in versions if v.get("html") and os.path.exists(os.path.join(RULES, v["html"]))]
    for v in usable:
        if v.get("predecessor_override"):
            v["predecessor"] = v["predecessor_override"]
            continue
        doc = v["document"]
        if doc == "ADD":
            v["predecessor"] = []
            continue
        if v.get("draft"):
            drafts = [d for d in usable if d.get("draft") and d["document"] == doc and d["version"].split(".")[0] ==
                      v["version"].split(".")[0] and (d.get("published") or "") < (v.get("published") or "")]
            if drafts:
                v["predecessor"] = [max(drafts, key=lambda d: d.get("published") or "")["id"]]
                continue
            finals = [d for d in usable if not d.get("draft") and d["status"] == "final" and not d.get("withdrawn") and d["document"] == doc and
                      (d.get("published") or "") <= (v.get("published") or "9999")]
            v["predecessor"] = [max(finals, key=date_key)["id"]] if finals else []
            continue
        same = [d for d in usable if not d.get("draft") and d["status"] == "final" and d["document"] == doc and
                date_key(d) < date_key(v) and (not d.get("withdrawn") or v.get("withdrawn"))]
        if same:
            v["predecessor"] = [max(same, key=date_key)["id"]]
        elif doc == "COMBINED":
            prev = []
            for pd in ("RER", "RPR"):
                cand = [d for d in usable if d["document"] == pd and d["status"] == "final" and date_key(d) < date_key(v)]
                if cand:
                    prev.append(max(cand, key=date_key)["id"])
            v["predecessor"] = prev
        else:
            v["predecessor"] = []
    for v in versions:
        if v not in usable:
            v.pop("predecessor", None)
    return by_id


def current_version(versions, today):
    finals = [v for v in versions if v["status"] == "final" and v["lang"] == "en" and not v.get("draft") and not v.get("withdrawn")
              and v.get("html") and v["document"] in ("COMBINED", "RER") and (v.get("effective") or "9999") <= today]
    return max(finals, key=date_key)["id"] if finals else None


def main():
    today = datetime.date.today().isoformat()
    if "--today" in sys.argv:
        today = sys.argv[sys.argv.index("--today") + 1]
    vpath = os.path.join(RULES, "versions.json")
    meta = json.load(open(vpath, encoding="utf-8"))
    versions = meta["versions"]

    # file facts
    for v in versions:
        if v.get("pdf"):
            p = os.path.join(RULES, v["pdf"])
            if os.path.exists(p):
                b = open(p, "rb").read()
                v["sha256"] = hashlib.sha256(b).hexdigest()
                v["bytes"] = len(b)
                v["pages"] = len(pymupdf.open(p))
        if v.get("html") and not os.path.exists(os.path.join(RULES, v["html"])):
            print("warning: %s has no HTML yet" % v["id"])
        for k in ("summary_pdf",):
            if v.get(k) and not os.path.exists(os.path.join(RULES, v[k])):
                print("warning: missing %s for %s" % (k, v["id"]))

    compute_predecessors(versions)
    cur = current_version(versions, today)
    meta["current"] = cur
    meta["generated"] = today

    os.makedirs(os.path.join(DATA, "units"), exist_ok=True)
    os.makedirs(os.path.join(DATA, "changes"), exist_ok=True)
    units = {}
    for v in versions:
        if v.get("html") and os.path.exists(os.path.join(RULES, v["html"])):
            units[v["id"]] = extract_units(os.path.join(RULES, v["html"]))
            json.dump(units[v["id"]], open(os.path.join(DATA, "units", v["id"] + ".json"), "w", encoding="utf-8"),
                      ensure_ascii=False, separators=(",", ":"))

    # mapping + changes
    mapping = {}
    review = []
    lineage_of = {}  # (vid, key) -> lineage id
    lineages = {}
    order = sorted([v for v in versions if v["id"] in units], key=lambda v: (date_key(v), v["id"]))
    stats = {"versions": {}, "sections": {}}
    for v in order:
        vid = v["id"]
        new = [u for u in units[vid] if u["t"] != "meta"]
        changes = {"id": vid, "predecessor": v.get("predecessor", []), "counts": {}, "items": []}
        claimed = set()
        for pid in v.get("predecessor", []):
            old = [u for u in units[pid] if u["t"] != "meta"]
            res = match_units(old, new)
            mapping[pid + ">" + vid] = [[old[i]["k"] if i is not None else None, new[j]["k"] if j is not None else None,
                                        s, c] for i, j, s, c in res if i is not None and j is not None]
            for i, j, s, c in res:
                if i is not None and j is not None:
                    if j in claimed:
                        continue
                    claimed.add(j)
                    o, n = old[i], new[j]
                    lid = lineage_of.get((pid, o["k"]))
                    if lid:
                        lineage_of[(vid, n["k"])] = lid
                    status = "same" if o["text"] == n["text"] else "changed"
                    moved = o["k"] != n["k"]
                    item = {"status": status, "key": n["k"], "old_key": o["k"], "old_version": pid, "sim": s,
                            "conf": c, "t": n["t"], "path": n["path"]}
                    if moved:
                        item["moved"] = True
                    if status == "changed":
                        item["diff"] = word_diff(o["text"], n["text"])
                    changes["items"].append(item)
                    if c == "low":
                        review.append("| %s → %s | `%s` → `%s` | %.2f | %s → %s |" % (
                            pid, vid, o["k"], n["k"], s, o["text"][:70].replace("|", "/"), n["text"][:70].replace("|", "/")))
                elif i is not None and j is None and len(v.get("predecessor", [])) == 1:
                    o = old[i]
                    changes["items"].append({"status": "removed", "old_key": o["k"], "old_version": pid, "t": o["t"],
                                             "path": o["path"], "text": o["text"]})
        if len(v.get("predecessor", [])) > 1:
            # combined successor of two documents: removed = units claimed by none
            for pid in v["predecessor"]:
                old = [u for u in units[pid] if u["t"] != "meta"]
                got = {m[0] for m in mapping[pid + ">" + vid]}
                for o in old:
                    if o["k"] not in got:
                        changes["items"].append({"status": "removed", "old_key": o["k"], "old_version": pid,
                                                 "t": o["t"], "path": o["path"], "text": o["text"]})
        for j, n in enumerate(new):
            if j not in claimed:
                if v.get("predecessor"):
                    changes["items"].append({"status": "added", "key": n["k"], "t": n["t"], "path": n["path"],
                                             "text": n["text"]})
            if (vid, n["k"]) not in lineage_of:
                lid = "%s@%s" % (n["k"], vid)
                lineage_of[(vid, n["k"])] = lid
        for n in new:
            lid = lineage_of[(vid, n["k"])]
            L = lineages.setdefault(lid, {"title": n["text"][:120], "t": n["t"], "versions": []})
            L["versions"].append([vid, n["k"]])
            if n["t"] == "heading":
                L["title"] = n["text"][:120]
        cnt = {}
        for it in changes["items"]:
            cnt[it["status"]] = cnt.get(it["status"], 0) + 1
            if it.get("moved"):
                cnt["moved"] = cnt.get("moved", 0) + 1
        changes["counts"] = cnt
        json.dump(changes, open(os.path.join(DATA, "changes", vid + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        # stats
        allw = sum(len(words(u["text"])) for u in new)
        my = sum(len(words(" ".join(u.get("marks", {}).get("Y", [])))) for u in units[vid])
        mg = sum(len(words(" ".join(u.get("marks", {}).get("G", [])))) for u in units[vid])
        stats["versions"][vid] = {
            "words": allw, "units": len(new),
            "headings": sum(1 for u in new if u["t"] == "heading"),
            "rules": sum(1 for u in new if u["t"] == "item"),
            "tables": sum(1 for u in new if u["t"] == "table"),
            "marked_yellow_words": my, "marked_green_words": mg,
            "changes": cnt,
        }
        # changes per top-level section (title of the first path element)
        titles = {u["k"]: u["text"] for u in units[vid] if u["t"] == "heading"}
        for it in changes["items"]:
            if it["status"] in ("changed", "added", "removed"):
                top = it["path"][0] if it.get("path") else None
                if it["status"] == "removed":
                    t2 = {u["k"]: u["text"] for u in units[it["old_version"]] if u["t"] == "heading"}
                    name = t2.get(top, top) if top else "(top)"
                else:
                    name = titles.get(top, top) if top else "(top)"
                name = re.sub(r"^Section \d+:\s*", "", name or "(top)")
                s = stats["sections"].setdefault(name, {})
                s[vid] = s.get(vid, 0) + 1

    # lineages: keep only multi-version ones fully, singles compact
    json.dump(lineages, open(os.path.join(DATA, "lineages.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    json.dump({"generated": today, "pairs": mapping}, open(os.path.join(DATA, "mapping.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump(stats, open(os.path.join(DATA, "stats.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    search = {vid: [[u["k"], u["text"]] for u in us if u["t"] != "meta"] for vid, us in units.items()}
    json.dump(search, open(os.path.join(DATA, "search.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    with open(os.path.join(DATA, "mapping-review.md"), "w", encoding="utf-8") as f:
        f.write("# Uncertain mappings (similarity < 0.8, different key)\n\n"
                "Generated by tools/build.py. Each row links a rule of an older version to the most similar rule of "
                "the next version. Most pairs are the same rule with reworded text; the lowest similarities are the most likely mismatches.\n\n"
                "| versions | keys | similarity | old → new text |\n|---|---|---|---|\n")
        f.write("\n".join(review) + "\n")

    # root copy of the version in force
    if cur:
        v = next(x for x in versions if x["id"] == cur)
        html = open(os.path.join(RULES, v["html"]), encoding="utf-8").read()
        html = html.replace('src="../img/', 'src="rules/img/')
        hdr = ("<!-- Generated by rules/tools/build.py from rules/html/%s.html: the WSDC rules version currently "
               "in force. Do not edit this file; edit the version file and run the build. -->\n" % cur)
        open(os.path.join(REPO, "registry_event_rules.html"), "w", encoding="utf-8").write(hdr + html)

    json.dump(meta, open(vpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(vpath, "a") as f:
        f.write("\n")
    print("current version: %s; %d versions with HTML; %d lineages; %d uncertain mappings" % (
        cur, len(units), len(lineages), len(review)))


if __name__ == "__main__":
    main()
