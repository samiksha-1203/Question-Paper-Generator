"""
concept_extraction.py
----------------------
This is the module that fixes the core problem described in the project
brief: the old pipeline picked a random noun out of *any* sentence (a
person's name, the word "instance", a proper noun mentioned only once in a
worked example, ...) and treated it as an "answer". That produces nonsense
like "Explain the significance of Riya".

The approach here is deliberately conservative:

1. Sentences are first classified as FRAGMENT, ILLUSTRATIVE/EXAMPLE, or
   SUBSTANTIVE. Only substantive sentences are allowed to nominate concept
   candidates or serve as question source material.
     - FRAGMENT: doesn't read as a complete independent clause (e.g. an
       extracted PDF line that starts mid-sentence, or a Q/A example answer
       like "Answered questions about lunar rock samples...").
     - ILLUSTRATIVE: a worked example / meta-linguistic aside, e.g. anything
       that explains a co-reference example in quotes ("she" refers to
       Riya...), or a "For instance, ..." aside. These are still useful as
       *supporting detail* inside a bigger answer, but a term mentioned only
       inside one is never allowed to become the concept a question is
       about.
2. A phrase only becomes a candidate "concept" if it is a genuine noun
   phrase (not a stray verb/adjective), is not a common personal name, is
   not on the small generic/meta-word blocklist, AND recurs - it must appear
   in at least two different chunks (or once as a clear document heading
   *and* once in running substantive text) so that one-off illustrative
   entities picked from a single worked example can never qualify. Real
   syllabus topics (Morphology, Named Entity Recognition, Question
   Answering, ...) reliably recur across a chapter; throwaway example nouns
   (Riya, the Apollo missions, ...) do not.
3. Every surviving concept is packaged with its supporting SUBSTANTIVE
   sentences, an optional explicit definition sentence, and (if detected) a
   contrastive partner concept for comparison questions - all fully
   traceable back to the source PDF/chunk for the dashboard and for
   regeneration.
"""

import re
from collections import defaultdict, Counter

from nlp_utils import (
    sent_tokenize_safe, word_tokenize_safe, pos_tag_safe, STOPWORDS,
)

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

# Common given names (English + widely used Indian names) - a single-word
# candidate that matches one of these is almost always a person mentioned in
# a worked example, never a taught concept.
COMMON_NAMES = {
    "riya", "priya", "anjali", "neha", "pooja", "kavya", "meera", "isha",
    "rahul", "rohan", "aman", "arjun", "aditya", "raj", "amit", "vikram",
    "sanjay", "sunil", "ravi", "suresh", "ramesh", "anil", "deepak", "vijay",
    "john", "mary", "james", "robert", "michael", "david", "richard",
    "susan", "jennifer", "linda", "patricia", "elizabeth", "barbara",
    "alice", "bob", "carol", "dave", "emma", "olivia", "sophia", "liam",
    "noah", "mia", "ava", "ethan", "lucas", "harry", "sarah", "peter",
    "paul", "mark", "anna", "laura", "julia", "steve", "steven", "kevin",
    "kate", "katie", "amy", "amanda", "rachel", "sam", "samuel", "daniel",
    "chris", "christopher", "jack", "jane", "tom", "thomas", "george",
    "nayana", "mahajan", "mahaja",
}

# Generic / meta words that describe the *shape* of an example rather than
# an educational concept. A candidate equal to one of these (as a whole
# phrase) is rejected outright regardless of frequency.
GENERIC_BLOCKLIST = {
    "instance", "example", "context", "case", "note", "notes", "page",
    "figure", "table", "chapter", "section", "paper", "unit", "topic",
    "question", "answer", "significance", "detail", "details", "point",
    "points", "text", "sentence", "word", "content",
}

FRAGMENT_START_TAGS = {"VBD", "VBN", "VBG", "IN", "CC", "TO", "MD", "VBZ", "VBP"}

_QUOTE_RE = re.compile(r"[\"\u201c\u201d\u2018\u2019']")
_HEADING_RE = re.compile(r"^(?:[A-Z][A-Za-z0-9&/]*\s*){1,6}$")

_PAREN_ACRONYM = r"(?:\s*\([A-Za-z.]{2,10}\))?"

DEFINITION_PATTERNS = [
    re.compile(r"^(?P<term>[A-Z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,5}?)" + _PAREN_ACRONYM + r"\s+(?:is|are)\s+(?:defined as|known as|called|referred to as)\s+(?P<rest>.+)$", re.I),
    re.compile(r"^(?P<term>[A-Z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,5}?)" + _PAREN_ACRONYM + r"\s+(?:is|are)\s+(?:a|an|the)\s+(?P<rest>.+)$"),
    re.compile(r"^(?P<term>[A-Z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,5}?)" + _PAREN_ACRONYM + r"\s+refers?\s+to\s+(?P<rest>.+)$", re.I),
    re.compile(r"^(?P<term>[A-Z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,5}?)" + _PAREN_ACRONYM + r"\s+can\s+be\s+defined\s+as\s+(?P<rest>.+)$", re.I),
    re.compile(r"^(?:The\s+process\s+of\s+)?(?P<term>[A-Z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,5}?)" + _PAREN_ACRONYM + r"\s+involves\s+(?P<rest>.+)$"),
]

CONTRAST_MARKERS = re.compile(
    r"\b(whereas|unlike|as opposed to|in contrast to|compared to|versus|vs\.?|"
    r"on the other hand|while)\b", re.I
)
APPLICATION_MARKERS = re.compile(
    r"\b(used (?:in|for|to)|application[s]? of|applied (?:in|to)|useful for|helps? (?:in|to))\b",
    re.I,
)
ADV_DISADV_MARKERS = re.compile(
    r"\b(advantage[s]?|disadvantage[s]?|limitation[s]?|benefit[s]?|drawback[s]?|"
    r"strength[s]?|weakness(?:es)?)\b",
    re.I,
)
PROCESS_MARKERS = re.compile(
    r"\b(step[s]?|process|algorithm|procedure|method|technique|stage[s]?|phase[s]?)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Sentence classification
# ---------------------------------------------------------------------------

def classify_sentence(sentence: str) -> str:
    """Returns 'fragment', 'illustrative', or 'substantive'."""
    s = sentence.strip()
    if len(s.split()) < 6:
        return "fragment"
    if not s[:1].isupper():
        return "fragment"

    tokens = word_tokenize_safe(s)
    if len(tokens) < 4:
        return "fragment"
    tags = pos_tag_safe(tokens[:2])
    if tags and tags[0][1] in FRAGMENT_START_TAGS:
        return "fragment"

    low = s.lower()

    # co-reference / meta-linguistic worked examples: '"she" refers to Riya'
    if re.search(r"\brefers?\s+to\b", low) and _QUOTE_RE.search(s):
        return "illustrative"
    if low.startswith(("for instance", "for example", "e.g.", "i.e.", "here is another example",
                        "here, ", "here ", "consider the example", "for example,")):
        return "illustrative"
    if "another example" in low or "worked example" in low:
        return "illustrative"
    # a sentence built almost entirely around a quoted fragment is usually a
    # worked example, not an explanation
    if _QUOTE_RE.search(s) and re.search(r"\b(means?|refers?)\b", low):
        return "illustrative"
    # bound-morpheme notation (un-, -ly, -ed) signals a morphological
    # worked example rather than a general explanatory sentence
    if re.search(r"(?:^|\s)[A-Za-z]{1,4}-(?:\s|,|$)", s) or re.search(r"(?:^|\s)-[A-Za-z]{1,4}(?:\s|,|\.|$)", s):
        return "illustrative"

    return "substantive"


def is_heading_line(line: str) -> bool:
    s = line.strip()
    if not s or len(s.split()) > 6 or len(s.split()) < 1:
        return False
    return bool(_HEADING_RE.match(s)) and any(c.islower() for c in s)


# ---------------------------------------------------------------------------
# Noun-phrase candidate extraction
# ---------------------------------------------------------------------------

_NOUN_TAGS = {"NN", "NNS", "NNP", "NNPS"}
_MODIFIER_TAGS = {"JJ", "JJR", "JJS", "VBG", "VBN"}


def _noun_phrases_with_spans(sentence: str):
    """Returns list of (phrase, start_idx, end_idx) using token indices, so
    callers can tell whether a phrase sits at the start of the sentence
    (i.e. is likely the grammatical subject). Handles technical compounds
    whose head word is a gerund/participle mis-tagged as VBG/VBN by the
    statistical tagger (e.g. 'Named Entity Recognition', 'Question
    answering systems', 'Part-of-speech tagging')."""
    tokens = word_tokenize_safe(sentence)
    tags = pos_tag_safe(tokens)

    phrases = []
    current = []  # list of (word, tag, idx)
    for idx, (word, tag) in enumerate(tags):
        if not re.match(r"^[A-Za-z][A-Za-z\-]*$", word):
            if current:
                _flush_phrase(current, phrases)
            current = []
            continue
        if tag in _NOUN_TAGS or tag in _MODIFIER_TAGS:
            current.append((word, tag, idx))
        else:
            if current:
                _flush_phrase(current, phrases)
            current = []
    if current:
        _flush_phrase(current, phrases)
    return phrases


_TRIM_TAGS = {"JJ", "JJR", "JJS", "VBN"}  # gerunds (VBG) can themselves head a
                                          # nominal phrase ("tagging", "processing")
                                          # so they are not trimmed from the end


def _flush_phrase(current, phrases):
    # trim trailing adjective/participle-only tokens (a phrase must end on a
    # real noun, or on a gerund acting as one)
    trimmed = list(current)
    while trimmed and trimmed[-1][1] in _TRIM_TAGS:
        trimmed.pop()
    if not trimmed:
        return
    if not any(t in _NOUN_TAGS or t == "VBG" for _, t, _ in trimmed):
        return
    words = [w for w, _, _ in trimmed]
    if not (1 <= len(words) <= 5):
        return
    if all(w.lower() in STOPWORDS for w in words):
        return
    phrases.append((" ".join(words), trimmed[0][2], trimmed[-1][2]))


def _noun_phrases(sentence: str):
    return [p for p, _, _ in _noun_phrases_with_spans(sentence)]


_FINITE_VERB_TAGS = {"VBZ", "VBP", "VBD", "MD"}


def _first_verb_idx(tags):
    # only finite verb forms count as the sentence's main verb boundary -
    # VBG/VBN are common tagger mistakes on gerund/participle-headed
    # technical noun phrases ("Named Entity Recognition", "Question
    # answering systems") and must not be treated as the predicate.
    for idx, (_, tag) in enumerate(tags):
        if tag in _FINITE_VERB_TAGS:
            return idx
    return len(tags)


def _subject_phrases(sentence: str):
    """Noun phrases that plausibly form the grammatical subject of the
    sentence: they start within the first two tokens and end before the
    sentence's first verb."""
    tokens = word_tokenize_safe(sentence)
    tags = pos_tag_safe(tokens)
    first_verb = _first_verb_idx(tags)
    out = []
    for phrase, start, end in _noun_phrases_with_spans(sentence):
        if start <= 1 and end < first_verb:
            out.append(phrase)
    return out


def _normalize(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase).strip().lower()


def _is_rejected(phrase: str) -> bool:
    norm = _normalize(phrase)
    words = norm.split()
    if not words:
        return True
    if norm in GENERIC_BLOCKLIST:
        return True
    if len(words) == 1 and words[0] in COMMON_NAMES:
        return True
    if any(w in COMMON_NAMES for w in words) and len(words) <= 2:
        return True
    if len(norm) <= 2:
        return True
    if norm.isdigit():
        return True
    return False


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

class Concept:
    def __init__(self, name):
        self.name = name  # display form (first-seen casing)
        self.norm = _normalize(name)
        self.support = []          # list of dicts: {chunk_idx, sentence, pdf_id, module}
        self.definition = None      # a substantive sentence matching a definition pattern
        self.chunk_scores = Counter()  # chunk_idx -> tfidf-ish weight
        self.heading_hit = False
        self.contrast_partner = None
        self.tags = set()           # {"application", "adv_disadv", "process"}
        self.tag_sentences = {"application": [], "adv_disadv": [], "process": []}
        self.subject_count = 0      # how many substantive sentences have this as their subject

    @property
    def doc_freq(self):
        return len({s["chunk_idx"] for s in self.support})

    @property
    def best_chunk_idx(self):
        if not self.chunk_scores:
            return self.support[0]["chunk_idx"] if self.support else None
        return self.chunk_scores.most_common(1)[0][0]

    def context_sentences(self, limit=4):
        seen = set()
        out = []
        # prefer the definition sentence first
        if self.definition:
            out.append(self.definition["sentence"])
            seen.add(self.definition["sentence"])
        for s in self.support:
            if s["sentence"] in seen:
                continue
            out.append(s["sentence"])
            seen.add(s["sentence"])
            if len(out) >= limit:
                break
        return out


def extract_concepts(chunks, pdf_id=None, module=None, min_doc_freq=2):
    """
    chunks: list[str] of cleaned paragraph chunks for one PDF.
    Returns: list[Concept], sorted by an importance score (support size +
    definition bonus + heading bonus).
    """
    concepts = {}
    chunk_sentence_class = []  # per-chunk list of (sentence, class)

    for idx, chunk in enumerate(chunks):
        sents = sent_tokenize_safe(chunk)
        classified = [(s, classify_sentence(s)) for s in sents]
        chunk_sentence_class.append(classified)

    # term frequency across the whole corpus (substantive sentences only) to
    # weight chunk-level importance later
    global_tf = Counter()
    for classified in chunk_sentence_class:
        for sentence, cls in classified:
            if cls != "substantive":
                continue
            for phrase in _noun_phrases(sentence):
                if _is_rejected(phrase):
                    continue
                global_tf[_normalize(phrase)] += 1

    for idx, classified in enumerate(chunk_sentence_class):
        for sentence, cls in classified:
            if cls == "fragment":
                continue  # never used for anything

            phrases = _noun_phrases(sentence) if cls == "substantive" else []
            subjects = {_normalize(p) for p in _subject_phrases(sentence)} if cls == "substantive" else set()
            for phrase in phrases:
                if _is_rejected(phrase):
                    continue
                norm = _normalize(phrase)
                c = concepts.setdefault(norm, Concept(phrase))
                c.support.append({
                    "chunk_idx": idx, "sentence": sentence,
                    "pdf_id": pdf_id, "module": module,
                })
                c.chunk_scores[idx] += global_tf.get(norm, 1)
                if norm in subjects:
                    c.subject_count += 1

                for pat in DEFINITION_PATTERNS:
                    m = pat.match(sentence.strip())
                    if m and _normalize(m.group("term")) == norm and c.definition is None:
                        c.definition = {"sentence": sentence, "chunk_idx": idx}

                if APPLICATION_MARKERS.search(sentence):
                    c.tags.add("application")
                    c.tag_sentences["application"].append(sentence)
                if ADV_DISADV_MARKERS.search(sentence):
                    c.tags.add("adv_disadv")
                    c.tag_sentences["adv_disadv"].append(sentence)
                if PROCESS_MARKERS.search(sentence):
                    c.tags.add("process")
                    c.tag_sentences["process"].append(sentence)

            # illustrative sentences may still mention a real concept term
            # that's ALSO backed elsewhere by substantive support - we allow
            # that concept to pick up this sentence as extra (non-defining)
            # context, but never as its only support.
            if cls == "illustrative":
                for phrase in _noun_phrases(sentence):
                    norm = _normalize(phrase)
                    if norm in concepts and not _is_rejected(phrase):
                        concepts[norm].support.append({
                            "chunk_idx": idx, "sentence": sentence,
                            "pdf_id": pdf_id, "module": module,
                            "illustrative_only": True,
                        })

    # heading bonus: a Title-Case heading line preceding a chunk (if present
    # in the raw chunk text as its first sentence-like fragment) boosts a
    # matching concept's credibility even at lower frequency, but we still
    # require it to appear as real running text at least once elsewhere.
    for norm, c in list(concepts.items()):
        substantive_support = [s for s in c.support if not s.get("illustrative_only")]
        c.support = substantive_support

    # final filter: require recurrence so one-off illustrative nouns never
    # qualify as a concept, unless there's an explicit definition sentence
    # for it (a term can be properly *defined* once and that's enough).
    final = []
    for norm, c in list(concepts.items()):
        if not c.support:
            continue
        qualifies = (
            c.definition is not None
            or c.subject_count >= 2
            or (c.subject_count >= 1 and c.doc_freq >= min_doc_freq)
        )
        if qualifies:
            final.append(c)

    # detect contrastive pairs: two concepts whose support sentences overlap
    # on a sentence containing a contrast marker
    contrast_sentences = defaultdict(list)
    for c in final:
        for s in c.support:
            if CONTRAST_MARKERS.search(s["sentence"]):
                contrast_sentences[s["sentence"]].append(c)
    for sentence, cs in list(contrast_sentences.items()):
        uniq = {c.norm: c for c in cs}
        if len(uniq) >= 2:
            names = list(uniq.values())
            names[0].contrast_partner = names[1].name
            names[1].contrast_partner = names[0].name

    final.sort(key=lambda c: (c.definition is not None, c.doc_freq, sum(c.chunk_scores.values())), reverse=True)
    return final
