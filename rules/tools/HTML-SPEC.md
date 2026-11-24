# HTML format for WSDC rules versions

Every rules version is stored as one **HTML fragment** in `rules/html/<id>.html`.
The fragments are shown by the viewer (`rules/viewer/`), embedded by the
WordPress plugin `goo1-wsdc` and by manage.worldsdc.com, and compared with each
other. Consistent structure and stable IDs matter more than visual fidelity.

## File

- UTF-8, no `<!DOCTYPE>`, `<html>`, `<head>`, `<body>`, no `<script>`, no `<style>`,
  no `style="…"` attributes, no classes except the ones listed here.
- Exactly one root element:

```html
<section class="wsdc-rules" data-version="2025.1A" data-document="RER" lang="en">
  …
</section>
```

  `data-document`: `RER` (Registry Event Rules), `RPR` (Points Registry Rules),
  `COMBINED` (2023+ single document), `ADD` (addendum).

## What to leave out

- The table of contents (the viewer builds its own).
- Running page headers, page footers, page numbers ("3 of 17"), watermarks ("DRAFT").
- The address block of old letterheads may stay as a `<p class="rules-letterhead">`.

## What to keep, exactly

- Every word of the rules text, in the original order, **with original spelling,
  typos, capitalisation and punctuation**. Do not fix, reword or modernise.
- Only repair hyphenation that was caused by a line break, and join lines into
  paragraphs.
- Curly quotes, dashes and symbols as in the PDF.
- Notes like "Rule changes effective … are highlighted in yellow." and version
  history lines ("Previous versions dated …") go into `<div class="rules-meta">`
  directly after the title.
- The footer identification (e.g. "WSDC Registry Event Rules, effective January 1,
  2025" + "RER Version 2025.1A") once at the very end:

```html
<footer class="rules-footer"><span>WSDC Registry Event Rules, effective January 1, 2025</span> <span>RER Version 2025.1A</span></footer>
```

## Structure

| PDF element | HTML |
|---|---|
| Document title | `<h2>` |
| Top level: "Overview", "Section 1: …", "APPENDIX", "Legend" | `<h3>` |
| Second level: "1.1. …", "WSDC", "Registry Events", "Definitions" under Overview (2023+, as in the PDF's own table of contents) | `<h4>` |
| Third level: "1.4.1 …", a single definition ("Venue") | `<h5>` |
| Paragraph | `<p>` |
| Numbered / lettered / roman list | `<ol>` with `<li data-list-text="1.">` (label exactly as printed: `1.`, `a.`, `i.`, `1)`, `(a)`) |
| Bullet list | `<ul>` with `<li>` |
| Chart / table | `<figure class="rules-chart" id="chart3"><figcaption>Chart 3: …</figcaption><table class="rules-table">…</table></figure>` (use `<thead>`/`<th>` for header rows, `colspan`/`rowspan` where the PDF merges cells) |
| Footnote / legend entry | `<li id="legend-3">` inside `<ol class="rules-legend">` under `<h3>Legend</h3>`; the reference in the text: `<sup><a id="mark-3" href="#legend-3">[3]</a></sup>` |
| Bold / italic / underline | `<strong>`, `<em>`, `<u>` |
| URL or e-mail in the text | `<a href="https://…">…</a>` / `<a href="mailto:…">…</a>` |
| Cross reference "see Section 3.4" / "Chart 3" | optional `<a href="#3.4">…</a>` — only if the target ID exists in the same file |
| Graphic that cannot be a table | `<img src="../img/<id>-<name>.png" alt="…">` (put the PNG into `rules/img/`) |
| Advancement chart (Chart 3, 2023+): blue level bar | `<span class="rules-level-bar">Champion</span>` inside the cell |
| Advancement chart arrows | `<img class="rules-arrow" src="../img/arrow_rules_up_allowed.png" alt="allowed">` / `…arrow_rules_up_required.png" alt="required">` (shared images, already in `rules/img/`) |

Do not wrap the whole document in extra `<div>`s. Nested lists go inside the parent `<li>`.

## IDs (stable anchors, used for comparing versions)

- Numbered headings use the number as printed, without trailing dot:
  `<h3 id="1">Section 1: …</h3>`, `<h4 id="1.1">1.1. …</h4>`, `<h5 id="1.4.1">1.4.1 …</h5>`.
- Numbered list items: parent ID + `.` + label without punctuation.
  Item 4 of 1.2 → `1.2.4`; item a under it → `1.2.4a`; item i under that → `1.2.4a-i`.
  Items of an unnumbered paragraph block take the ID of the closest numbered heading.
- Definitions: `definition_<slug>` (`definition_venue`, `definition_timedistancerule`;
  slug = lowercase letters/digits of the title, no separators).
- Unnumbered headings (old documents, Overview): `s-<slug>` with hyphens
  (`s-points-registry`, `s-overview`).
- Charts: `chart1`, `chart2`, … as numbered in the PDF; unnumbered tables `table-<slug>`.
- IDs are unique inside the file. If the PDF itself repeats a number (e.g. two lists under one heading both
  starting at 1.), the SECOND one in document order gets the suffix `-2` (`4.1` … then `4.1-2` …), a third `-3`.
- Never invent numbering that is not in the PDF.

## Highlights (the WSDC's yellow / green change markers)

- Words on a yellow background → `<mark>…</mark>`; on green → `<mark class="green">…</mark>`.
- Mark exactly the highlighted words (see `marks.json` / `[[Y:…]]` in `text.txt`).
- A `<mark>` must not cross element boundaries: split it per paragraph, list item,
  table cell.
- Do not add marks that are not in the PDF.

## Checks

`python3 rules/tools/check.py rules/html/<id>.html` must report no errors.
It checks tags, IDs, anchors, forbidden markup, word coverage against the PDF
and the highlights.
