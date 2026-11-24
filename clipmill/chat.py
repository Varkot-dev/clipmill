"""Chat replay parsing and spike detection.

A funny or wild moment shows up in chat a few seconds later as a burst of messages
and reaction emotes (KEKW, OMEGALUL, W, WHAT...). We measure each 5 second bucket
against the stream's own normal chat speed, so a small streamer and a huge one are
judged on the same scale.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REACTIONS = {
    "funny": {
        "kekw", "kek", "kekl", "lul", "lulw", "omegalul", "icant", "lmao", "lmfao", "lol",
        "pepelaugh", "xdd", "😂", "🤣", "💀", "dead", "kekwait", "omegakek",
    },
    "hype": {"w", "ww", "www", "pog", "poggers", "pogchamp", "pagman", "ez", "clean", "goated", "letsgo", "🔥"},
    "shock": {"what", "?", "??", "???", "monkas", "monkaw", "d:", "nooo", "omg", "wtf", "aware", "huh", "😳"},
    "sad": {"l", "sadge", "o7", "f", "pepehands", "widepeeposad", "😭"},
}
TOKEN_TO_CAT = {tok: cat for cat, toks in REACTIONS.items() for tok in toks}
_HAHA = re.compile(r"^(ha){2,}h?$|^a?(ha)+$")


@dataclass
class Message:
    t: float
    text: str
    author: str = ""


@dataclass
class Spike:
    t: float                 # time of the chat peak, seconds into the VOD
    zscore: float
    rate: float              # weighted activity in the peak bucket
    category: str            # funny, hype, shock, sad or general
    sample: list[str] = field(default_factory=list)
    score: float = 0.0       # zscore plus boosts, used for ranking


def load_chat(path: str | Path) -> list[Message]:
    """Reads a TwitchDownloader chat JSON (or a simple [{t, text}] list)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [Message(float(m["t"]), str(m["text"]), str(m.get("author", ""))) for m in data]
    out = []
    for c in data.get("comments", []):
        msg = c.get("message", {})
        body = msg.get("body")
        if body is None:
            body = "".join(f.get("text", "") for f in msg.get("fragments", []))
        commenter = c.get("commenter") or {}
        author = commenter.get("name") or commenter.get("_id") or commenter.get("display_name") or ""
        out.append(Message(float(c["content_offset_seconds"]), body, str(author)))
    return out


def classify(text: str) -> Counter:
    counts: Counter = Counter()
    for raw in text.split():
        tok = raw.strip(".,!").lower()
        cat = TOKEN_TO_CAT.get(tok)
        if cat is None and _HAHA.match(tok):
            cat = "funny"
        if cat:
            counts[cat] += 1
    return counts


def find_spikes(
    messages: list[Message],
    duration_s: float | None = None,
    bucket_s: float = 5,
    baseline_min: float = 10,
    min_z: float = 3.0,
    min_gap_s: float = 90,
    min_chatters: int = 4,
    fallback_top: int = 5,
) -> list[Spike]:
    """Adapts to chat size: small chats get wider time buckets and need fewer distinct
    reactors. If nothing clears the bar, the strongest few moments are returned anyway
    (with their lower scores) so the judge can still look at them."""
    if not messages:
        return []
    duration_s = duration_s or max(m.t for m in messages) + bucket_s
    per_min = len(messages) / max(duration_s / 60, 1)
    if per_min < 20:
        bucket_s = bucket_s * min(6.0, 20 / max(per_min, 0.1))
    if per_min < 30:
        min_chatters = min(min_chatters, 3)
    spikes = _detect(messages, duration_s, bucket_s, baseline_min, min_z, min_gap_s, min_chatters)
    if not spikes and fallback_top:
        loose = _detect(messages, duration_s, bucket_s, baseline_min, 1.5, min_gap_s, 2)
        spikes = sorted(sorted(loose, key=lambda s: -s.zscore)[:fallback_top], key=lambda s: s.t)
    return spikes


def _detect(messages, duration_s, bucket_s, baseline_min, min_z, min_gap_s, min_chatters) -> list[Spike]:
    duration_s = duration_s or max(m.t for m in messages) + bucket_s
    n = int(duration_s // bucket_s) + 1
    activity = np.zeros(n)
    cats = [Counter() for _ in range(n)]
    texts: list[list[str]] = [[] for _ in range(n)]
    # Count each chatter once per bucket (for messages and for reactions), so one
    # person spamming KEKW cannot fake a spike. Without author names, every message counts.
    seen: list[set] = [set() for _ in range(n)]
    reactors: list[set] = [set() for _ in range(n)]
    for m in messages:
        i = min(int(m.t // bucket_s), n - 1)
        c = classify(m.text)
        if m.author and c:
            reactors[i].add(m.author)
        if m.author:
            if m.author in seen[i]:
                texts[i].append(m.text)
                continue
            seen[i].add(m.author)
        activity[i] += 1 + 2 * min(sum(c.values()), 1)
        cats[i] += c
        texts[i].append(m.text)

    # Rolling median and MAD as the "normal" chat speed around each bucket.
    half = max(1, int(baseline_min * 60 / bucket_s) // 2)
    z = np.zeros(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window = activity[lo:hi]
        med = np.median(window)
        mad = np.median(np.abs(window - med)) * 1.4826
        z[i] = (activity[i] - med) / max(mad, 1.0)

    # Local maxima above the threshold, strongest first, at least min_gap apart.
    # When names are known, a real moment needs several different people reacting
    # (KEKW, W, WHAT...), not one person spamming.
    has_authors = any(m.author for m in messages)

    def crowd(i: int) -> int:
        return len(set().union(*reactors[max(0, i - 1): i + 2]))

    peaks = [i for i in range(n) if z[i] >= min_z and z[i] == z[max(0, i - 2): i + 3].max()
             and (not has_authors or crowd(i) >= min_chatters)]
    peaks.sort(key=lambda i: -z[i])
    gap = int(min_gap_s // bucket_s)
    chosen: list[int] = []
    for i in peaks:
        if all(abs(i - j) >= gap for j in chosen):
            chosen.append(i)

    spikes = []
    for i in sorted(chosen):
        around = sum((cats[j] for j in range(max(0, i - 1), min(n, i + 2))), Counter())
        category = around.most_common(1)[0][0] if around else "general"
        sample = [t for j in range(max(0, i - 1), min(n, i + 2)) for t in texts[j]][:25]
        spikes.append(
            Spike(t=i * bucket_s + bucket_s / 2, zscore=float(z[i]), rate=float(activity[i]),
                  category=category, sample=sample, score=float(z[i]))
        )
    return spikes


def boost_with_viewer_clips(spikes: list[Spike], viewer_clips: list[dict], boost: float = 2.0,
                            lead_s: float = 50, messages: list[Message] | None = None,
                            max_new: int = 8, min_views: int = 3) -> list[Spike]:
    """A viewer clip near a spike is strong evidence it is good. Popular viewer clips with no
    matching chat spike become candidates of their own: viewers already picked that moment."""
    used = set()
    for s in spikes:
        hits = [i for i, c in enumerate(viewer_clips) if s.t - lead_s - 30 <= c["offset_s"] <= s.t + 10]
        if hits:
            used.update(hits)
            s.score += boost + np.log10(1 + sum(viewer_clips[i]["views"] for i in hits))
            title = viewer_clips[max(hits, key=lambda i: viewer_clips[i]["views"])].get("title", "")
            if title:
                s.sample = [f"[a viewer clipped this moment and titled it: {title}]"] + s.sample
    extra = sorted((c for i, c in enumerate(viewer_clips) if i not in used and c["views"] >= min_views),
                   key=lambda c: -c["views"])
    taken: list[float] = []
    for c in extra:
        t = c["offset_s"] + min(c.get("duration_s", 30), 60) - 3  # the payoff sits near the clip's end
        if any(abs(t - x) < 60 for x in taken) or any(abs(t - s.t) < 60 for s in spikes):
            continue
        sample = [m.text for m in (messages or []) if t - 20 <= m.t <= t + 10][:20]
        spikes.append(Spike(t=t, zscore=0.0, rate=0.0, category="viewer clip",
                            sample=[f"[a viewer clipped this moment and titled it: {c.get('title', '')}]"] + sample,
                            score=boost + 2 * np.log10(1 + c["views"])))
        taken.append(t)
        if len(taken) >= max_new:
            break
    return sorted(spikes, key=lambda s: -s.score)
