"""
nlp_utils.py
------------
Shared low-level NLP helpers: NLTK bootstrap, safe tokenization/POS-tagging,
stopwords, and small TF-IDF utilities used across the pipeline.
"""

import re
import math
from collections import Counter

import nltk
from nltk.corpus import stopwords

_REQUIRED_NLTK = [
    ("tokenizers/punkt", "punkt"),
    ("tokenizers/punkt_tab", "punkt_tab"),
    ("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger"),
    ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
    ("corpora/wordnet", "wordnet"),
    ("corpora/omw-1.4", "omw-1.4"),
    ("corpora/stopwords", "stopwords"),
]


def ensure_nltk_data():
    for path, pkg in _REQUIRED_NLTK:
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                nltk.download(pkg, quiet=True)
            except Exception:
                pass  # degrade gracefully; regex fallbacks kick in below


ensure_nltk_data()

try:
    STOPWORDS = set(stopwords.words("english"))
except Exception:
    STOPWORDS = set(
        "a an the is are was were be been being of in on at to for and or "
        "but if then so than that this these those it its as by with from "
        "not no can will would should could may might have has had do does did "
        "we you they i he she him her them his hers our your their".split()
    )

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")


def sent_tokenize_safe(text: str):
    try:
        from nltk.tokenize import sent_tokenize
        return sent_tokenize(text)
    except Exception:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def word_tokenize_safe(text: str):
    try:
        from nltk.tokenize import word_tokenize
        return word_tokenize(text)
    except Exception:
        return _WORD_RE.findall(text)


def pos_tag_safe(tokens):
    try:
        return nltk.pos_tag(tokens)
    except Exception:
        return [(t, "NNP" if t[:1].isupper() else "NN") for t in tokens]


def tokenize_words(text: str, drop_stopwords=True):
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if drop_stopwords:
        words = [w for w in words if w not in STOPWORDS]
    return words


def tfidf_sentence_scores(sentences):
    """Lightweight TF-IDF importance score per sentence (no sklearn dependency
    needed for this specific use, kept dependency-free for speed)."""
    tokenized = [tokenize_words(s) for s in sentences]
    df = Counter()
    for words in tokenized:
        for w in set(words):
            df[w] += 1
    n = len(sentences) or 1
    scores = []
    for words in tokenized:
        tf = Counter(words)
        score = 0.0
        for w, c in tf.items():
            idf = math.log((n + 1) / (df[w] + 1)) + 1
            score += c * idf
        score = score / (len(words) + 1e-6)
        scores.append(score)
    return scores


def cosine_sim(counter_a: Counter, counter_b: Counter) -> float:
    common = set(counter_a) & set(counter_b)
    num = sum(counter_a[w] * counter_b[w] for w in common)
    denom = math.sqrt(sum(v * v for v in counter_a.values())) * math.sqrt(sum(v * v for v in counter_b.values()))
    return num / denom if denom else 0.0


def text_vector(text: str) -> Counter:
    return Counter(tokenize_words(text))


def is_near_duplicate(text: str, existing_vectors, threshold=0.82) -> bool:
    v = text_vector(text)
    return any(cosine_sim(v, ev) >= threshold for ev in existing_vectors)
