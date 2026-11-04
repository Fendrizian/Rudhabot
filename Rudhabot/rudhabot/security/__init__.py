"""Server security: Rudhabot's raid, spam, phishing and anti-nuke defences, moderation records and recovery tools.

Everything in this package except `discord_port` is plain Python with no Discord dependency, so the same code runs in
the bot, in the tests and in the wargame simulator (`rudhabot.ml.security.sim`), which replays thousands of simulated
server days through the real detectors.

Layers (each depends only on the ones above it):
    perms, model, events          permission bits; plain snapshots of a guild; normalised events
    text, links                   text normalisation, fingerprints (MinHash), URL / attachment analysis
    reasons, signals              plain-language reason codes; the structured evidence detectors emit
    policy, clearance, levels     per-server configuration; staff tiers; the five threat levels
    detectors, antinuke           deterministic detection (joins, messages, audit log, webhooks)
    responses, engine             proportionate responses and the event -> signal -> incident -> plan pipeline
    lockdown, snapshots, posture  scoped channel lockdowns, server snapshots + restore, the permission audit
    actions                       the action adapter: authorisation at execution, shadow mode, pacing, outcomes
    discord_port                  the only module that talks to discord.py

Trust boundary: models (Clairvoyance) and the LLM only add evidence, rank alerts or draft summaries. Every action is
planned by deterministic code, checked against the actor's clearance when it runs, and recorded with its outcome.
"""
