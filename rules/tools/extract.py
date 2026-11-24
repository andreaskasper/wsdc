#!/usr/bin/env python3
"""Extract text, highlights and layout hints from a WSDC rules PDF.

Usage: extract.py <file.pdf> <outdir> [--pages 1-8] [--dpi 110]

Writes into <outdir>:
  text.txt     one line per PDF text line, prefixed with layout hints:
                 p<page> x<indent> s<fontsize> [B]  <text>
               highlighted words are wrapped as [[Y:...]] (yellow) or [[G:...]] (green)
  plain.txt    plain text, one line per PDF line (for diffing / word coverage)
  marks.json   list of highlighted runs {page, color, text}
  page-NN.png  page images for visual checks

The WSDC marks changes with filled rectangles (RGB 1,1,0 yellow / 0,1,0 green)
drawn behind the text, not with PDF highlight annotations.
"""
import json
import os
import sys

import pymupdf

YELLOW = "Y"
GREEN = "G"


def rect_color(fill):
    if not fill or len(fill) != 3:
        return None
    r, g, b = fill
    if r > 0.85 and g > 0.85 and b < 0.35:
        return YELLOW
    if r < 0.2 and g > 0.85 and b < 0.35:
        return GREEN
    return None


def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < n]


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    pdf, outdir = args[0], args[1]
    pages_spec = None
    dpi = 110
    if "--pages" in args:
        pages_spec = args[args.index("--pages") + 1]
    if "--dpi" in args:
        dpi = int(args[args.index("--dpi") + 1])
    os.makedirs(outdir, exist_ok=True)
    doc = pymupdf.open(pdf)
    hint_lines, plain_lines, marks = [], [], []

    for pno in parse_pages(pages_spec, len(doc)):
        page = doc[pno]
        rects = []
        for dr in page.get_drawings():
            c = rect_color(dr.get("fill"))
            if c:
                rects.append((pymupdf.Rect(dr["rect"]), c))
        # span lookup for font info
        spans = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    spans.append((pymupdf.Rect(s["bbox"]), s["size"], bool(s["flags"] & 16) or "Bold" in s["font"]))
        words = page.get_text("words", sort=True)
        # group into lines by (block, line)
        lines = {}
        order = []
        for w in words:
            key = (w[5], w[6])
            if key not in lines:
                lines[key] = []
                order.append(key)
            lines[key].append(w)
        # sort lines by y then x
        order.sort(key=lambda k: (round(lines[k][0][1] / 3), lines[k][0][0]))
        cur_run = None
        for key in order:
            ws = sorted(lines[key], key=lambda w: w[0])
            out_tokens = []
            size = 0
            bold_all = True
            for w in ws:
                r = pymupdf.Rect(w[:4])
                cpt = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                color = None
                for rr, c in rects:
                    if rr.contains(cpt):
                        color = c
                        break
                fs, bold = 0, False
                for sr, ssize, sbold in spans:
                    if sr.contains(cpt):
                        fs, bold = ssize, sbold
                        break
                size = max(size, fs)
                bold_all = bold_all and bold
                out_tokens.append((w[4], color))
                if color:
                    if cur_run and cur_run["color"] == color and cur_run["page"] == pno + 1:
                        cur_run["text"] += " " + w[4]
                    else:
                        cur_run = {"page": pno + 1, "color": color, "text": w[4]}
                        marks.append(cur_run)
                else:
                    cur_run = None
            # render tokens with run markers
            txt, i = [], 0
            while i < len(out_tokens):
                t, c = out_tokens[i]
                if c:
                    j = i
                    grp = []
                    while j < len(out_tokens) and out_tokens[j][1] == c:
                        grp.append(out_tokens[j][0])
                        j += 1
                    txt.append("[[%s:%s]]" % (c, " ".join(grp)))
                    i = j
                else:
                    txt.append(t)
                    i += 1
            x0 = int(ws[0][0])
            hint_lines.append("p%d x%d s%.1f%s  %s" % (pno + 1, x0, size, " B" if bold_all else "", " ".join(txt)))
            plain_lines.append(" ".join(w[4] for w in ws))
        pix = page.get_pixmap(dpi=dpi)
        pix.save(os.path.join(outdir, "page-%02d.png" % (pno + 1)))

    with open(os.path.join(outdir, "text.txt"), "w") as f:
        f.write("\n".join(hint_lines) + "\n")
    with open(os.path.join(outdir, "plain.txt"), "w") as f:
        f.write("\n".join(plain_lines) + "\n")
    with open(os.path.join(outdir, "marks.json"), "w") as f:
        json.dump(marks, f, indent=1, ensure_ascii=False)
    print("%s: %d lines, %d highlighted runs (%d words)" % (
        pdf, len(plain_lines), len(marks), sum(len(m["text"].split()) for m in marks)))


if __name__ == "__main__":
    main()
