"""Approved knowledge base: versioned documents, metadata filters, embedding search with a disk cache."""

import hashlib
import json
from datetime import date

import numpy as np

from . import config

CACHE = config.DATA / ".embeddings.json"


def parse(path):
    """Markdown file with a small front matter (id, title, lang, topic, valid_from, valid_to)."""
    _, head, body = path.read_text().split("---", 2)
    meta = dict(line.split(":", 1) for line in head.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    meta["valid_from"] = date.fromisoformat(meta["valid_from"])
    meta["valid_to"] = date.fromisoformat(meta.get("valid_to", "2099-12-31"))
    meta["text"] = body.strip()
    return meta


class Knowledge:
    def __init__(self, llm, folder=config.DATA / "knowledge"):
        self.llm = llm
        self.docs = [parse(p) for p in sorted(folder.glob("*.md"))]
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
