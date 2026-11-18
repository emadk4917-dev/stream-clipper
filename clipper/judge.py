"""Claude judges candidate moments: score 1-10, exact cuts, title, hook.

Two stages (as in the launch plan): a cheap model screens every candidate, the main model
only judges the ones that pass. Both are configurable in .env.
"""
from __future__ import annotations

import json

import anthropic

from .candidates import transcript_lines
from .config import env, env_float

JUDGE_MODEL = env("JUDGE_MODEL", "claude-opus-5-5")
SCREEN_MODEL = env("SCREEN_MODEL", "claude-haiku-5-5")  # "" = no screening, judge everything
SCREEN_MIN = env_float("SCREEN_MIN", 5)

RULES = """You are the head editor of a short-form clipping team. You pick moments from a live
stream that will go viral as YouTube Shorts / TikTok / Reels, and you write them up.

A great clip:
- makes sense to someone who has never seen this streamer (no inside context needed)
- grabs attention in the first 2 seconds (a reaction, shocking line, question, or the action starting)
- has a payoff: punchline, rage, clutch play, wild take, genuine reaction, chaos
- is 15-45 s (never over 59 s)

Score 1-10 honestly. 8+ = would genuinely go viral. 5-7 = solid, postable. <5 = skip.
Score <5 (always) for: dead air, rambling, reading donations, the payoff missing from the transcript,
tragedies, slurs, sexual content, gambling promos, copyrighted music as the main audio, game story
spoilers/cutscenes, and money glitches or exploits.

Cut points: start ~0.3 s before the first word of the set-up line, end ~0.5 s after the payoff,
using the absolute seconds in the transcript. Cut straight into the action, no slow lead-in.
Also give a longer 61-90 s window (tiktok_start/tiktok_end) ONLY if the moment genuinely holds up for
that long (needed for TikTok Creator Rewards); otherwise set both to 0.

title: <=70 chars, streamer name + emotion/curiosity, no clickbait the clip doesn't deliver.
hook: <=6 words shown big for the first 3 seconds, or "" if the first line already hooks.
tags: 3-6 lowercase hashtags without '#'. Follow any campaign rules you are given."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"}, "score": {"type": "number"}, "reason": {"type": "string"},
            "start": {"type": "number"}, "end": {"type": "number"},
            "tiktok_start": {"type": "number"}, "tiktok_end": {"type": "number"},
            "title": {"type": "string"}, "hook": {"type": "string"}, "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "score", "reason", "start", "end", "tiktok_start", "tiktok_end",
                     "title", "hook", "description", "tags"],
        "additionalProperties": False}}},
    "required": ["picks"], "additionalProperties": False,
}

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "score": {"type": "number"}},
        "required": ["id", "score"], "additionalProperties": False}}},
    "required": ["scores"], "additionalProperties": False,
}

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from .env
    return _client


def _cands_text(cands: list[dict]) -> str:
    parts = []
    for c in cands:
        lines = transcript_lines(c["words"]) or ["(no speech: gameplay, music or a scream)"]
        parts.append(f"### {c['id']} (peak at {c['peak']:.0f}s, loudness/chat spike score {c['hype']})\n"
                     f"context available {c['context_start']:.0f}s-{c['context_end']:.0f}s\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _ask(model: str, system: str, user: str, schema: dict, effort: str) -> dict | None:
    kwargs = dict(model=model, max_tokens=16000, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    if model.startswith(("claude-opus-5", "claude-sonnet-5-5", "claude-fable")):
        # if a safety classifier declines, retry on a fallback model inside the same call
        resp = client().beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client().messages.create(**kwargs)
    if resp.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text) if text else None


def screen(cands: list[dict], streamer: str) -> dict[str, float]:
    """Cheap first pass: id -> score. Everything passes if screening is switched off."""
    if not SCREEN_MODEL or not cands:
        return {c["id"]: 10.0 for c in cands}
    out = _ask(SCREEN_MODEL, RULES, f"Streamer: {streamer}. Quickly score each moment 1-10.\n\n" + _cands_text(cands),
               SCREEN_SCHEMA, "low")
    return {s["id"]: s["score"] for s in (out or {}).get("scores", [])}


def judge(cands: list[dict], streamer: str, campaign_rules: str = "", game: str = "") -> list[dict]:
    """Full judgement on candidates that passed screening. Returns picks (score >= 5 recommended)."""
    if not cands:
        return []
    scores = screen(cands, streamer)
    keep = [c for c in cands if scores.get(c["id"], 0) >= SCREEN_MIN]
    if not keep:
        return []
    user = (f"Streamer: {streamer}\nWhat they're playing: {game or 'unknown'}\n"
            f"Campaign rules: {campaign_rules or 'none given'}\n\n"
            f"Judge every moment below and return one pick per moment (score it low if it's weak).\n\n"
            + _cands_text(keep))
    out = _ask(JUDGE_MODEL, RULES, user, PICK_SCHEMA, env("JUDGE_EFFORT", "medium"))
    picks = (out or {}).get("picks", [])
    by_id = {c["id"]: c for c in keep}
    for p in picks:
        c = by_id.get(p["id"])
        if c:
            p["peak"] = c["peak"]
            # keep cuts sane: inside the stream and 8-59 s long
            if not (8 <= p["end"] - p["start"] <= 59):
                p["start"], p["end"] = c["suggested_start"], c["suggested_end"]
    return [p for p in picks if p["id"] in by_id]
