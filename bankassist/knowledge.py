"""Approved knowledge base: versioned documents, metadata filters, embedding search with a disk cache."""

import hashlib
import json
from datetime import date

import numpy as np

from . import config

CACHE = config.DATA / ".embeddings.json"


def load(path):
    """Approved documents with metadata: id, title, lang, topic, validity dates, text."""
    docs = json.loads(path.read_text())
    for d in docs:
        d["valid_from"] = date.fromisoformat(d["valid_from"])
        d["valid_to"] = date.fromisoformat(d.get("valid_to", "2099-12-31"))
    return docs


class Knowledge:
    def __init__(self, llm, path=config.DATA / "knowledge.json"):
        self.llm = llm
        self.docs = load(path)
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        missing = [d for d in self.docs if self._key(d) not in self.cache]
        if missing:  # embed once, reuse across restarts
            vectors, _ = llm.embed([f"{d['title']}\n{d['text']}" for d in missing])
            for d, v in zip(missing, vectors):
                self.cache[self._key(d)] = v
            CACHE.write_text(json.dumps(self.cache))
        self.matrix = np.array([self.cache[self._key(d)] for d in self.docs])
        self.matrix /= np.linalg.norm(self.matrix, axis=1, keepdims=True)

    @staticmethod
    def _key(doc):
        return hashlib.sha1(f"{config.EMBED_MODEL}:{doc['title']}\n{doc['text']}".encode()).hexdigest()

    def search(self, query, language=None, topic=None, k=3, today=None):
        """Filter first (validity dates, language, topic), then rank by cosine similarity."""
        today = today or date.today()
        vector, usage = self.llm.embed([query])
        q = np.array(vector[0])
        scores = self.matrix @ (q / np.linalg.norm(q))
        hits = []
        for i in np.argsort(-scores):
            d = self.docs[i]
            if not (d["valid_from"] <= today <= d["valid_to"]):
                continue                     # outdated tariff or policy: never served
            if language and d["lang"] != language:
                continue
            if topic and d["topic"] != topic:
                continue
            hits.append({"id": d["id"], "title": d["title"], "text": d["text"], "score": round(float(scores[i]), 3)})
            if len(hits) == k:
                break
        return hits, usage
