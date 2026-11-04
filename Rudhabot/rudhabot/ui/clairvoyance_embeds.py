"""One view of all of Clairvoyance: every model Rudhabot trains and serves, its health, and what it has learned here.

Used by `/clairvoyance status` and `/security tools action:models`.
"""
from __future__ import annotations

from typing import Optional

import discord

from rudhabot.ui.branding import brand
from rudhabot.ui.theme import CLAIRVOYANCEICON, Colors


def _line(ok: bool, text: str) -> str:
    return ("🟢 " if ok else "🔴 ") + text


def status_embed(services, guild_id: Optional[int] = None) -> discord.Embed:
    embed = discord.Embed(title=f"{CLAIRVOYANCEICON} Clairvoyance", color=Colors.CLAIRVOYANCE,
                          description="Rudhabot's own machine learning: trained here, served here, never on members' "
                                      "messages. Every part is advisory and has its own fallback.")
    # ---- Cloudfall value network
    st = services.clairvoyance.status()
    tcg = f"`{st['model']}`" + (f" · {st['params']:,} params" if st.get("params") else "")
    if not st["ready"]:
        tcg += f"\n⚠️ {st['load_error']} (heuristic AI in use)"
    m = st.get("metrics")
    if m:
        tcg += f"\nheld-out AUC {m['auc']:.3f} · ECE {m['ece']:.3f}"
    tcg += f"\n{sum(v for k, v in st['calls'].items() if not k.endswith('_fallback'))} calls · p95 {st['latency_ms_p95']:.1f} ms"
    embed.add_field(name="Cloudfall (AI opponent, coach, win bar, Deck Doctor)", value=_line(st["ready"], tcg), inline=False)
    # ---- intent router
    router = services.intents
    learn = getattr(router, "outcomes", None)
    from rudhabot.ml.intent_data import INTENTS
    intent = (f"{len(INTENTS)} intents · confident from {router.confident:.0%}" if router.ready
              else f"unavailable: {router.error}")
    if learn:
        intent += f"\n{learn.get('offered', 0)} suggestions · {learn.get('run', 0)} run · {learn.get('clarified', 0)} " \
                  "clarifying questions"
    embed.add_field(name="Intent router (/ask, @mentions)", value=_line(router.ready, intent), inline=False)
    # ---- anomaly monitor
    reviews = services.reviews
    det = reviews.detector
    calib = getattr(reviews, "calibration", {}) or {}
    anomaly = (f"fitted on {det.fitted_on:,} hourly windows" if det.fitted_on else "cold start: rules only") + \
              f" · bar {det.threshold:.2f}"
    if calib.get("resolved"):
        anomaly += f" (learned from {calib['resolved']} verdicts: {calib.get('dismissed', 0)} dismissed)"
    anomaly += f"\n{reviews.stats.get('judged', 0):,} windows judged · {reviews.stats.get('flags', 0)} signals"
    embed.add_field(name="Anomaly monitor (/review)", value=_line(True, anomaly), inline=False)
    # ---- security models
    sec = services.security
    if sec.models is not None:
        ms = sec.models.status(sec.config(guild_id) if guild_id else None)
        lines = [f"message: {ms['message']}", f"incident: {ms['incident']}", f"ranker: {ms['ranker']}"]
        if guild_id:
            cal = sec.calibration(guild_id)
            if cal.get("verdicts"):
                lines.append(f"learned from {cal['verdicts']} staff verdicts · alerts {cal['precision']:.0%} real · "
                             f"model bar {cal['message_bar']:.2f} / {cal['incident_bar']:.2f}")
            else:
                lines.append("no staff verdicts yet: mark alerts Real / False alarm to teach it")
        ready = ms["message"] == "ready"
        embed.add_field(name="Security (message risk, incident detector, alert ranker)",
                        value=_line(ready, "\n".join(lines))[:1024], inline=False)
    # ---- assistant
    a = services.assistant
    embed.add_field(name="Language model (optional, OpenAI)",
                    value=_line(a.enabled, f"`{a.model.model}`" if a.enabled else f"off: {a.disabled_reason}")[:1024],
                    inline=False)
    embed.add_field(name="Also", value="Finance tips in `/balance` and `/bank card info` (your own history only).",
                    inline=False)
    return brand(embed, "status")
