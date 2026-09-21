"""Deterministic local narrowing before bounded Jev classification."""
from __future__ import annotations
import re

STOP = {"a","an","and","are","as","at","be","by","for","from","in","is","it","no","not","of","on","or","the","this","to","use","with"}


def _tokens(value):
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1 and token not in STOP}


def retrieve(profile, task, context="", available_ids=None, *, limit=12):
    allowed = set(profile.eligible_ids)
    if available_ids is not None:
        allowed &= set(available_ids)
    query = _tokens(task + " " + context)
    lowered = (task + " " + context).lower()
    ranked = []
    for sid in sorted(allowed):
        entry = profile.entries.get(sid)
        if entry is None:
            continue
        name = sid.rsplit(":", 1)[-1].replace("-", " ")
        name_tokens = _tokens(name)
        description_tokens = _tokens(entry.description)
        score = 8.0 if name and name in lowered else 0.0
        score += 4.0 * len(query & name_tokens)
        score += 1.0 * len(query & description_tokens)
        # Prefer more specific cards when overlap is otherwise equal.
        score += min(1.0, len(query & description_tokens) / max(1, len(description_tokens)))
        if score > 0:
            ranked.append((score, sid))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:limit]
    return {"candidate_ids": [sid for _, sid in selected],
            "ranks": [{"id": sid, "rank": index + 1, "score": round(score, 4)}
                      for index, (score, sid) in enumerate(selected)],
            "eligible_count": len(allowed), "retrieved_count": len(selected),
            "rejected_count": max(0, len(allowed) - len(selected)), "limit": limit}
