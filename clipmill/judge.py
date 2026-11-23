"""Decides whether a chat spike is a real, self-contained moment, and where to cut it.

The default judge calls Claude through the Claude Code CLI (`claude -p`), which runs
on your logged-in Claude plan. Do not add --bare: bare mode ignores your login and
needs an API key instead. In GitHub Actions the same login comes from the
CLAUDE_CODE_OAUTH_TOKEN secret (made once with `claude setup-token`).
"""

from __future__ import annotations

import json
import os
import logging
import shutil
import subprocess
from dataclasses import dataclass

from .transcribe import Word, as_timed_text

log = logging.getLogger("clipmill")

SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "start_s": {"type": "number"},
        "end_s": {"type": "number"},
        "title": {"type": "string"},
        "category": {"type": "string", "enum": ["funny", "hype", "shock", "wholesome", "rage", "other"]},
        "spoiler_risk": {"type": "boolean"},
        "reason": {"type": "string"},
        "hook": {"type": "string"},
    },
    "required": ["keep", "score", "start_s", "end_s", "title", "category", "spoiler_risk", "reason", "hook"],
}

PROMPT = """You pick moments from {game} livestreams to post as YouTube Shorts and TikToks.

Streamer: {display}
Viewers reacted in chat at about {spike:.0f}s into this window (category guess: {category}).
Things the channel is testing this week (count it as a plus if the moment is about one): {focus}

Sample of chat during the spike:
{chat}

Transcript of the window (timestamps are seconds from the start of the window):
{transcript}

Decide:
- keep: true only if this is a complete moment a stranger would enjoy with no context:
  a clear setup and a payoff (a joke, a reaction, a fail, a chase, an argument, a clutch play).
- start_s / end_s: where to cut, in window seconds. Start right before the setup, end
  just after the payoff or the laugh. Never start or end mid-sentence. 15 to 60 seconds.
- score: 1 to 10 for how likely it is to do well as a Short.
- title: under 70 characters, plain and specific, no clickbait lies, no emojis.
- spoiler_risk: true if the moment is mainly a story cutscene, the game's ending, or a big
  plot reveal. Rockstar takes those down, so they must not be kept. Gameplay and the
  streamer's own reaction are fine.
- reason: one sentence.
- hook: 3 to 8 words shown at the top of the video for the whole clip. It gives a stranger
  the context and makes them want to see the payoff (for example "He thought the cop was
  an NPC"). Plain words, no emojis, no hashtags, do not give away the punchline.
"""


@dataclass
class Verdict:
    keep: bool
    score: int
    start_s: float
    end_s: float
    title: str
    category: str
    spoiler_risk: bool
    reason: str
    hook: str = ""


def snap_to_words(words: list[Word], start: float, end: float, min_gap: float = 0.25) -> tuple[float, float]:
    """Moves cut points into the nearest pause so captions and speech are not cut off."""
    if not words:
        return start, end
    starts = [w.start for i, w in enumerate(words) if i == 0 or w.start - words[i - 1].end >= min_gap]
    ends = [w.end for i, w in enumerate(words) if i == len(words) - 1 or words[i + 1].start - w.end >= min_gap]
    s = min(starts, key=lambda t: abs(t - start))
    e = min(ends, key=lambda t: abs(t - end))
    if e - s < 8:  # snapping collapsed the clip, keep the judge's original cut
        return start, end
    return max(0.0, s - 0.15), e + 0.4


def heuristic(spike_rel: float, words: list[Word], zscore: float, window_len: float) -> Verdict:
    start, end = snap_to_words(words, max(0.0, spike_rel - 35), min(window_len, spike_rel + 8))
    return Verdict(
        keep=zscore >= 2.5, score=max(1, min(10, int(zscore) + 3)), start_s=start, end_s=end,
        title="", category="other", spoiler_risk=False, reason=f"chat spike z={zscore:.1f}",
    )


def ask_claude(prompt: str, claude_bin: str = "claude", model: str = "", timeout: int = 180) -> dict:
    binary = shutil.which(claude_bin)
    if not binary:
        raise FileNotFoundError("Claude Code CLI not found. Install it and log in (see SETUP.md).")
    cmd = [binary, "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--permission-mode", "dontAsk"]
    if model:
        cmd += ["--model", model]
    env = os.environ.copy()
    tok = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if tok:  # a token copied from a wrapped terminal line can carry spaces or line breaks
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(tok.split())
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        detail = proc.stderr[-300:]
        try:
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                data = next((m for m in reversed(data) if m.get("type") == "result"), {})
            detail = f"{data.get('subtype', '')}: {str(data.get('result', ''))[:300]} {detail}"
        except Exception:
            detail = proc.stdout[:300] + " " + detail
        raise RuntimeError(f"claude -p failed: {detail.strip()}")
    return parse_claude_output(proc.stdout)


def parse_claude_output(stdout: str) -> dict:
    data = json.loads(stdout)
    if isinstance(data, list):  # some versions print the whole message list
        data = next((m for m in reversed(data) if m.get("type") == "result"), {})
    out = data.get("structured_output")
    if not out:
        raise RuntimeError(f"no structured_output in Claude response: {str(data)[:300]}")
    return out


def judge(*, words: list[Word], chat_sample: list[str], spike_rel: float, category: str,
          zscore: float, window_len: float, streamer, cfg_judge: dict) -> Verdict:
    if cfg_judge.get("mode", "claude") != "claude":
        return heuristic(spike_rel, words, zscore, window_len)
    prompt = PROMPT.format(
        game=streamer.game, display=streamer.display, spike=spike_rel, category=category,
        focus=", ".join(streamer.focus) or "nothing specific",
        chat="\n".join(chat_sample[:25]) or "(none)",
        transcript=as_timed_text(words) or "(no speech detected)",
    )
    if getattr(streamer, "notes", ""):
        prompt += f"\nCampaign brief from the streamer: {streamer.notes}\n"
    try:
        out = ask_claude(prompt, cfg_judge.get("claude_bin", "claude"), cfg_judge.get("model", ""))
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        # Never let a missing login or a Claude outage stop the run; fall back to chat-only scoring.
        log.warning("Claude judge unavailable (%s); using the chat-spike heuristic", str(e)[:200])
        return heuristic(spike_rel, words, zscore, window_len)
    v = Verdict(**{k: out.get(k, "") for k in SCHEMA["required"]})
    v.start_s, v.end_s = snap_to_words(words, max(0.0, v.start_s), min(window_len, v.end_s))
    if v.spoiler_risk:
        v.keep = False
    return v
