"""Dependency-free lexical sparse vectors shared by indexing and retrieval."""

import hashlib
import math
import re
from collections import Counter
from typing import Dict

SPARSE_DIM = 1 << 20


def text_to_sparse_vector(text: str) -> Dict[int, float]:
    """Hash Chinese characters, Chinese bigrams, and alphanumeric terms."""
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    terms = chinese + [
        "".join(chinese[index:index + 2])
        for index in range(len(chinese) - 1)
    ]
    terms.extend(re.findall(r"[a-z0-9]+", normalized))

    counts = Counter(terms)
    sparse: Dict[int, float] = {}
    for term, count in counts.items():
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % SPARSE_DIM
        sparse[index] = sparse.get(index, 0.0) + 1.0 + math.log(count)
    return sparse
