import math
import re
from collections import Counter
from typing import NamedTuple

VOWELS     = frozenset("aeiou")
HEX_CHARS  = frozenset("0123456789abcdef")
DIGITS     = frozenset("0123456789")
CONSONANTS = frozenset("bcdfghjklmnpqrstvwxyz")


COMMON_BIGRAMS = frozenset([
    "th", "he", "in", "er", "an", "re", "on", "en",
    "at", "ou", "ed", "ha", "to", "or", "it", "is",
    "hi", "es", "ng", "ne",
])


class DomainFeatures(NamedTuple):

    length: int
    entropy: float
    norm_entropy: float
    digit_ratio: float
    vowel_ratio: float
    unique_char_ratio: float
    consonant_run_max: int
    digit_run_max: int
    bigram_hit_ratio: float
    has_repeated_chars: int
    digit_count: int
    length_bin: int
    subdomain_count: int

    hyphen_count: int
    hyphen_ratio: float
    is_hex_pattern: int

    ends_with_digits: int


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _max_run(s: str, char_set: frozenset) -> int:
    max_run = cur = 0
    for ch in s:
        if ch in char_set:
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 0
    return max_run


def _bigram_hit_ratio(s: str) -> float:
    if len(s) < 2:
        return 0.0
    bigrams = [s[i:i + 2] for i in range(len(s) - 1)]
    return sum(1 for bg in bigrams if bg in COMMON_BIGRAMS) / len(bigrams)


def extract(domain: str) -> DomainFeatures:
    domain = domain.strip().lower().lstrip(".")
    parts  = domain.split(".")

    subdomain_count  = len(parts)
    label            = parts[0]
    n                = len(label)

    ends_with_digits = int(bool(label) and label[-1] in DIGITS)

    if n == 0:
        return DomainFeatures(
            length=0, entropy=0.0, norm_entropy=0.0,
            digit_ratio=0.0, vowel_ratio=0.0, unique_char_ratio=0.0,
            consonant_run_max=0, digit_run_max=0,
            bigram_hit_ratio=0.0, has_repeated_chars=0,
            digit_count=0, length_bin=0, subdomain_count=subdomain_count,
            hyphen_count=0, hyphen_ratio=0.0,
            is_hex_pattern=0, ends_with_digits=ends_with_digits,
        )

    digit_count  = sum(1 for c in label if c in DIGITS)
    vowel_count  = sum(1 for c in label if c in VOWELS)
    entropy      = _shannon_entropy(label)
    unique_chars = len(set(label))
    norm_entropy = entropy / math.log2(unique_chars) if unique_chars > 1 else 0.0
    hyphen_count = label.count("-")
    length_bin   = 0 if n < 6 else (1 if n <= 15 else 2)

    return DomainFeatures(
        length=n,
        entropy=entropy,
        norm_entropy=norm_entropy,
        digit_ratio=digit_count / n,
        vowel_ratio=vowel_count / n,
        unique_char_ratio=unique_chars / n,
        consonant_run_max=_max_run(label, CONSONANTS),
        digit_run_max=_max_run(label, DIGITS),
        bigram_hit_ratio=_bigram_hit_ratio(label),
        has_repeated_chars=int(bool(re.search(r"(.)\1{2,}", label))),
        digit_count=digit_count,
        length_bin=length_bin,
        subdomain_count=subdomain_count,
        hyphen_count=hyphen_count,
        hyphen_ratio=hyphen_count / n,
        is_hex_pattern=int(n >= 8 and all(c in HEX_CHARS for c in label)),
        ends_with_digits=ends_with_digits,
    )


def to_list(f: DomainFeatures) -> list:
    return list(f)


FEATURE_NAMES = DomainFeatures._fields