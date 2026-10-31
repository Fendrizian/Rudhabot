"""Rudhabot: a Discord economy / gambling bot.

Layering (each layer only depends on the ones above it):

    settings, errors, clock          configuration and tiny shared types
    domain/                          pure game/business rules (no Discord, no file I/O)
    persistence/                     JSON-file repositories
    services/                        application services that orchestrate domain + persistence
    ui/, views/                      Discord presentation helpers, embeds, interactive components
    cogs/                            slash commands (thin: parse input -> call services -> render)
    container.py, app.py             composition root: wires everything together
"""
