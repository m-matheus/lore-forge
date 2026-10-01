"""Deterministic script lint: the quality gate that the reference channel lacks.

Runs over ``{section_id: text}`` and reports, per section and sentence:
- ``banned``: channel banned phrases (``banned_phrases.txt``).
- ``repeated_ngrams``: 5-word sequences used more than once anywhere in the script
  (catches recycled images and turns of phrase across a two-hour text).
- ``openers``: the same first two words starting 3+ sentences in a section, or two
  consecutive paragraphs starting with the same word.
- ``overused``: content words far above normal frequency (excluding names).
- ``copied``: any 8-word sequence shared with a reference transcript. We extract
  knowledge from competitors, never wording; this makes that rule checkable.

Findings carry the offending sentence so the script step can rewrite ONLY those.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each even ever every
few for from further had has have having he her here hers herself him himself his how i if in
into is it its itself just less like may me might more most much must my myself never no nor
not now of off on once one only or other our ours ourselves out over own same she should so
some still such than that the their theirs them themselves then there these they this those
through to too under until up upon us very was we were what when where which while who whom
whose why will with within without would yet you your yours yourself yourselves
""".split())


@dataclass
class Finding:
    kind: str
    section: str
    sentence: str
    detail: str


@dataclass
class LintReport:
    findings: list[Finding] = field(default_factory=list)
    word_counts: dict[str, int] = field(default_factory=dict)

    def by_section(self, section: str) -> list[Finding]:
        return [f for f in self.findings if f.section == section]

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.kind for f in self.findings))

    def to_dict(self) -> dict:
        return {"counts": self.counts(), "word_counts": self.word_counts,
                "findings": [asdict(f) for f in self.findings]}


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def word_count(text: str) -> int:
    return len(words(text))


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in paragraphs(text):
        out += [s.strip() for s in _SENT_SPLIT.split(para) if s.strip()]
    return out


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _norm_tokens(text: str) -> list[str]:
    return [w.lower().strip("'-") for w in words(text)]


def _ngrams(tokens: list[str], n: int):
    for i in range(len(tokens) - n + 1):
        yield tuple(tokens[i:i + n])


def _content_ngram(gram: tuple[str, ...]) -> bool:
    """At least two non-stopwords: 'at the end of the' is grammar, not a phrase."""
    return sum(1 for t in gram if t not in STOPWORDS) >= 2


# --- Individual checks ---------------------------------------------------------------

def check_banned(sections: dict[str, str], patterns) -> list[Finding]:
    out = []
    for sid, text in sections.items():
        for sent in sentences(text):
            for p in patterns:
                m = p.search(sent)
                if m:
                    out.append(Finding("banned", sid, sent, m.group(0)))
    return out


def check_repeated_ngrams(sections: dict[str, str], n: int = 5,
                          allow: set[str] | None = None) -> list[Finding]:
    """Flag every occurrence after the first of a repeated content n-gram."""
    allow = {a.lower() for a in (allow or set())}
    seen: dict[tuple, str] = {}
    out = []
    for sid, text in sections.items():
        for sent in sentences(text):
            flagged = False
            for gram in _ngrams(_norm_tokens(sent), n):
                if not _content_ngram(gram) or " ".join(gram) in allow:
                    continue
                if gram in seen and not flagged:
                    out.append(Finding("repeated_ngrams", sid, sent,
                                       f"'{' '.join(gram)}' also in {seen[gram]}"))
                    flagged = True
                seen.setdefault(gram, sid)
    return out


def check_openers(sections: dict[str, str], max_same: int = 2) -> list[Finding]:
    out = []
    for sid, text in sections.items():
        by_opener: dict[str, list[str]] = defaultdict(list)
        for sent in sentences(text):
            toks = _norm_tokens(sent)[:2]
            if len(toks) == 2:
                by_opener[" ".join(toks)].append(sent)
        for opener, sents in by_opener.items():
            if len(sents) > max_same:
                for s in sents[max_same:]:
                    out.append(Finding("openers", sid, s, f"'{opener}' opens {len(sents)} sentences"))
        paras = paragraphs(text)
        for prev, cur in zip(paras, paras[1:]):
            a, b = _norm_tokens(prev)[:1], _norm_tokens(cur)[:1]
            if a and a == b:
                out.append(Finding("openers", sid, sentences(cur)[0],
                                   f"consecutive paragraphs both open with '{a[0]}'"))
    return out


def check_overused(sections: dict[str, str], *, per_thousand: float = 2.5,
                   min_count: int = 15, names: set[str] | None = None) -> list[Finding]:
    """Content words used far more than prose normally allows (names excluded).

    The thresholds are tuned on a real 18k-word script: at 1.2/1k ordinary words
    ("night", "small", "people") drown the signal; at 2.5/1k only genuine tics surface
    ("rather" 3.3/1k, "something" 3.2/1k).
    """
    names = {n.lower() for n in (names or set())}
    all_text = "\n\n".join(sections.values())
    toks = words(all_text)
    total = max(1, len(toks))
    capitalized = Counter(t for t in toks if t[0].isupper())
    lower = Counter(t.lower() for t in toks)
    out = []
    for w, c in lower.items():
        if w in STOPWORDS or len(w) < 5 or w in names or c < min_count:
            continue
        if capitalized.get(w.capitalize(), 0) > c * 0.6:     # mostly a proper noun
            continue
        if c / total * 1000 > per_thousand:
            out.append(Finding("overused", "*", "", f"'{w}' x{c} ({c / total * 1000:.1f}/1k words)"))
    return out


def check_copied(sections: dict[str, str], references: list[str], n: int = 8) -> list[Finding]:
    ref_grams: set[tuple] = set()
    for ref in references:
        ref_grams.update(_ngrams(_norm_tokens(ref), n))
    if not ref_grams:
        return []
    out = []
    for sid, text in sections.items():
        for sent in sentences(text):
            for gram in _ngrams(_norm_tokens(sent), n):
                if gram in ref_grams and _content_ngram(gram):
                    out.append(Finding("copied", sid, sent, f"'{' '.join(gram)}' is in a reference"))
                    break
    return out


def lint(sections: dict[str, str], *, banned_patterns=(), references: list[str] | None = None,
         names: set[str] | None = None, allow_ngrams: set[str] | None = None) -> LintReport:
    report = LintReport(word_counts={sid: word_count(t) for sid, t in sections.items()})
    report.findings += check_banned(sections, banned_patterns)
    report.findings += check_repeated_ngrams(sections, allow=allow_ngrams)
    report.findings += check_openers(sections)
    report.findings += check_overused(sections, names=names)
    report.findings += check_copied(sections, references or [])
    return report
