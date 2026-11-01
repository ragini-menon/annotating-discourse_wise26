#!/usr/bin/env python3
"""
tei_extract.py — Extract clean NLP-ready text from TEI XML.

Resolves editorial markup at the XML level, then uses contextual
signals around milestone elements (lb/pb/cb) to determine whether
they are intra-word breaks (join) or inter-word breaks (space).
No static word-list required.

Usage:
    python tei_extract.py input.xml [output.txt] [options]

Options:
    --keep-header       Retain <teiHeader> (default: remove)
    --prefer-sic        Prefer <sic> over <corr> in <choice> elements
    --separator SEP     String between paragraphs (default: newline)
    --no-strip          Disable per-paragraph whitespace stripping
    --keep-editorial    Skip editorial markup resolution
"""

import argparse
import re
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag


# ---------------------------------------------------------------------------
# XML loading
# ---------------------------------------------------------------------------

def load_xml(path: str, remove_header: bool = True) -> BeautifulSoup:
    with open(path, "r", encoding="utf-8") as fh:
        soup = BeautifulSoup(fh.read(), "xml")
    if remove_header and soup.teiHeader:
        soup.teiHeader.decompose()
    return soup


# ---------------------------------------------------------------------------
# Editorial markup resolution (in-place on the tree)
# ---------------------------------------------------------------------------

def _resolve_choice(tag: Tag, prefer_sic: bool) -> None:
    """Replace a <choice> with its preferred reading (corr or sic)."""
    corr = tag.find("corr", recursive=False)
    sic  = tag.find("sic",  recursive=False)
    first, second = (sic, corr) if prefer_sic else (corr, sic)

    chosen = None
    for candidate in (first, second):
        if candidate is not None and candidate.get_text(strip=True):
            chosen = candidate
            break

    if chosen is not None:
        tag.replace_with(chosen)
        chosen.unwrap()
    else:
        tag.decompose()


def resolve_editorial_markup(soup: BeautifulSoup, prefer_sic: bool = False) -> None:
    """
    Resolve all editorial markup in-place:
      <choice>   -> preferred reading (corr > sic by default)
      <subst>    -> <add> child (correction), else <del> child (original)
      <del>      -> removed entirely
      <add>      -> unwrapped (text flows inline, no extra space)
      <abbr>     -> unwrapped (keep abbreviated form)
      <expan>    -> unwrapped (keep expanded form)
      <unclear>  -> unwrapped (keep best reading available)
      <supplied> -> unwrapped
      <gap>      -> removed (lacuna)
      <note>     -> removed (editorial metadata)
      <fw>       -> removed (forme work / catchwords)
      all others -> unwrapped (persName, title, hi, seg, ref, etc.)
    """
    # <choice> first, before any <abbr>/<expan> unwrapping
    for tag in list(soup.find_all("choice")):
        _resolve_choice(tag, prefer_sic)

    # <subst>: prefer <add> (correction) over <del> (original)
    for tag in list(soup.find_all("subst")):
        add  = tag.find("add", recursive=False)
        del_ = tag.find("del", recursive=False)
        preferred = add if add is not None else del_
        if preferred is not None:
            tag.replace_with(preferred)
            preferred.unwrap()
        else:
            tag.decompose()

    # <del>: remove entirely
    for tag in list(soup.find_all("del")):
        tag.decompose()

    # <add>: unwrap so text is inline with no BeautifulSoup space injection
    for tag in list(soup.find_all("add")):
        tag.unwrap()

    # <gap>: remove (lacuna)
    for tag in list(soup.find_all("gap")):
        tag.decompose()

    # <note>, <fw>: editorial metadata — discard
    for name in ("note", "fw"):
        for tag in list(soup.find_all(name)):
            tag.decompose()

    # Everything else: unwrap (keep text, discard the wrapper tag)
    for name in (
        "abbr", "expan", "unclear", "supplied",
        "persName", "placeName", "title", "name",
        "foreign", "term", "hi", "emph", "mentioned",
        "said", "span", "num", "ref", "ptr", "pb",
    ):
        for tag in list(soup.find_all(name)):
            tag.unwrap()


# ---------------------------------------------------------------------------
# Milestone (lb / cb) intra-word join heuristic
# ---------------------------------------------------------------------------

_WORD_TAIL = re.compile(
    r"[A-Za-z\u00c0-\u024f\u1e00-\u1effœæŒÆ'\u2019]+"
    r"$"
)
_WORD_HEAD = re.compile(
    r"^[A-Za-z\u00c0-\u024f\u1e00-\u1effœæŒÆ'\u2019]+"
)

_BOUNDARY_BEFORE = frozenset(".!?:;,»")
_BOUNDARY_AFTER  = re.compile(r"^[A-Z\u00c0-\u00d6\u00d8-\u00de0-9\u00b6(]")

# Maximum combined fragment length to consider a join.
_MAX_COMBINED = 20

_INLINE_TAGS = frozenset((
    "persName", "placeName", "title", "name", "foreign", "term",
    "hi", "emph", "mentioned", "said", "span", "num", "ref",
    "add", "supplied", "unclear", "abbr", "expan",
))

_BLOCK_TAGS = frozenset(("lb", "cb", "pb", "p", "div", "body"))


def _get_text_neighbour(node: Tag, direction: str) -> str:
    """
    Walk siblings in `direction` ('prev' or 'next'), collecting text from
    inline tags, until a NavigableString, a block/milestone element, or the
    end of siblings is reached.

    This handles cases where an inline tag (e.g. <persName>, <add>) sits
    between an <lb> and its neighbouring text node.
    """
    sibling = node.previous_sibling if direction == "prev" else node.next_sibling

    while sibling is not None:
        if isinstance(sibling, NavigableString):
            return str(sibling)
        if isinstance(sibling, Tag):
            if sibling.name in _BLOCK_TAGS:
                return ""
            if sibling.name in _INLINE_TAGS:
                text = sibling.get_text()
                if text.strip():
                    return text
        sibling = (
            sibling.previous_sibling if direction == "prev"
            else sibling.next_sibling
        )

    return ""


def _should_join(before: str, after: str) -> bool:
    return False

def resolve_milestones(soup: BeautifulSoup) -> None:
    """
    Replace each <lb> / <cb> with nothing (intra-word join) or a space
    (word boundary). Strips encoder whitespace around intra-word breaks.
    """
    for ms in list(soup.find_all(["lb", "cb"])):
        before = _get_text_neighbour(ms, "prev")
        after  = _get_text_neighbour(ms, "next")

        if _should_join(before, after):
            prev = ms.previous_sibling
            nxt  = ms.next_sibling
            if isinstance(prev, NavigableString):
                prev.replace_with(NavigableString(str(prev).rstrip()))
            if isinstance(nxt, NavigableString):
                nxt.replace_with(NavigableString(str(nxt).lstrip()))
            ms.decompose()
        else:
            ms.replace_with(NavigableString(" "))


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(
    soup: BeautifulSoup,
    paragraph_separator: str = "\n",
    strip: bool = True,
) -> str:
    """
    Collect text from every <p> element.

    All markup has been resolved in-place, so descendants are plain
    NavigableString nodes. Concatenate directly (no separator) to preserve
    intra-word adjacency, then normalise whitespace.
    """
    paragraphs = []
    for p in soup.find_all("p"):
        raw = "".join(s for s in p.strings)
        if strip:
            raw = raw.strip()
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{2,}", "\n", raw)
        if raw:
            paragraphs.append(raw)

    return paragraph_separator.join(paragraphs)


def repair_legacy_splits(text: str) -> str:
    from collections import Counter
    words = re.findall(r"[A-Za-zÀ-ÿœæ\u2018\u2019'-]+", text)
    bigrams = Counter((words[i], words[i+1]) for i in range(len(words)-1))
    counts = Counter(words)
    repairs = []
    for (a, b), n in bigrams.items():
        c = a + b
        if len(a) >= 2 and len(b) >= 2 and counts[c] >= n and counts[c] > 0:
            if len(c) <= 18 or counts[c] > 2 * n:
                repairs.append((a, b, c))
    repairs = sorted(set(repairs), key=lambda x: -(len(x[0]) + len(x[1])))
    for a, b, c in repairs:
        text = re.sub(r"\b" + re.escape(a) + r" " + re.escape(b) + r"\b", c, text)
    return text

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input",  help="Path to TEI XML file")
    p.add_argument(
        "output", nargs="?", default=None,
        help="Output path (default: INPUT with .txt extension)",
    )
    p.add_argument(
        "--keep-header", action="store_true",
        help="Retain <teiHeader> (default: remove)",
    )
    p.add_argument(
        "--prefer-sic", action="store_true",
        help="Prefer <sic> over <corr> in <choice> elements",
    )
    p.add_argument(
        "--separator", default="\n",
        help="String inserted between paragraphs (default: newline)",
    )
    p.add_argument(
        "--no-strip", action="store_true",
        help="Disable per-paragraph whitespace stripping",
    )
    p.add_argument(
        "--keep-editorial", action="store_true",
        help="Skip editorial markup resolution",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    output_path = args.output or str(Path(args.input).with_suffix(".txt"))

    bar = "=" * 56
    print(bar)
    print("  TEI TEXT EXTRACTOR")
    print(bar)
    print(f"  Input  : {args.input}")
    print(f"  Output : {output_path}")
    print(bar)

    print("Loading & parsing XML ...", end=" ", flush=True)
    soup = load_xml(args.input, remove_header=not args.keep_header)
    print("✓")

    if not args.keep_editorial:
        print("Resolving editorial markup ...", end=" ", flush=True)
        resolve_editorial_markup(soup, prefer_sic=args.prefer_sic)
        print("✓")

    print("Resolving milestone line-breaks ...", end=" ", flush=True)
    resolve_milestones(soup)
    print("✓")

    print("Extracting text ...", end=" ", flush=True)
    text = extract_text(soup, paragraph_separator=args.separator,
                        strip=not args.no_strip)
    print(f"✓  ({len(text):,} characters)")

    print("Repairing split tokens ...", end=" ", flush=True)
    text = repair_legacy_splits(text)
    print(f"✓  ({len(text):,} characters)")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(text, encoding="utf-8")
    print(f"Saved -> {output_path}")
    print(bar)


if __name__ == "__main__":
    main()