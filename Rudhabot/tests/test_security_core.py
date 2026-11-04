"""Server security, pure layer: permission bits, text fingerprints, link and file analysis, clearance, threat levels,
configuration, detectors, anti-nuke and the engine's planning. No Discord, no network."""
import unittest

import discord

from rudhabot.security import perms as P
from rudhabot.security.clearance import Refused, Tier, check, resolve_tier, tier_from_permissions
from rudhabot.security.engine import GuildEngine
from rudhabot.security.events import Attachment, AuditEntry, MemberJoin, Message, Tick
from rudhabot.security.levels import Level, ThreatState
from rudhabot.security.links import analyse_attachment, analyse_text
from rudhabot.security.lockdown import apply_change, plan_lockdown, plan_restore
from rudhabot.security.fake import demo_guild
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.posture import audit
from rudhabot.security.reasons import REASONS, explain
from rudhabot.security.snapshots import capture, diff
from rudhabot.security.text import CoordinationIndex, MinHasher, content_hash, name_shape, normalise, skeleton, snippet

T0 = 1_780_000_000.0


def msg(i, author, text, ts, channel=10, member_days=30.0, tier=0, **kw):
    return Message(1, channel, 10_000 + i, author, ts, content=text, author_tier=tier,
                   joined_at=ts - member_days * 86400, created_at=ts - 400 * 86400, **kw)


class PermissionBitsTests(unittest.TestCase):
    def test_bits_match_discord(self):
        for name in ("administrator", "kick_members", "ban_members", "manage_channels", "manage_guild",
                     "view_audit_log", "send_messages", "manage_messages", "mention_everyone", "manage_roles",
                     "manage_webhooks", "moderate_members", "manage_threads", "send_messages_in_threads",
                     "create_public_threads", "create_private_threads", "add_reactions", "send_polls"):
            self.assertEqual(getattr(P, name.upper()), discord.Permissions(**{name: True}).value, name)
        self.assertIn("Administrator", P.names(P.ADMINISTRATOR | P.BAN_MEMBERS))


class TextTests(unittest.TestCase):
    def test_normalisation_folds_lookalikes_and_invisible_characters(self):
        self.assertEqual(normalise("Fr​ee  NІTRO"), "free nitro")        # zero-width + Cyrillic І
        self.assertEqual(skeleton("d1sc0rd"), "dlscord")
        self.assertEqual(content_hash("Buy NOW!!!"), content_hash("buy now"))
        self.assertEqual(name_shape("John_Doe1234"), "Aa_Aa9")
        self.assertLessEqual(len(snippet("x" * 500)), 200)

    def test_minhash_similarity_and_coordination_across_accounts(self):
        h = MinHasher()
        a = h.signature("claim your free nitro gift here before it expires")
        b = h.signature("claim your free nitro gift here before it expires!!")
        c = h.signature("the weather in the capital is lovely today")
        self.assertGreater(MinHasher.similarity(a, b), 0.7)
        self.assertLess(MinHasher.similarity(a, c), 0.2)
        index = CoordinationIndex()
        base = "check out this amazing server for free stuff"
        self.assertIsNone(index.add(T0, 1, 5, 1, base))
        self.assertIsNone(index.add(T0 + 1, 1, 5, 2, base))                 # same author: not coordination
        cluster = index.add(T0 + 2, 2, 5, 3, base + " now")
        self.assertEqual(cluster.authors, {1, 2})
        self.assertIsNone(index.add(T0 + 999, 3, 5, 4, base))               # the window has passed


class LinkTests(unittest.TestCase):
    def test_phishing_patterns_are_scored_and_explained(self):
        cases = {
            "free nitro https://dlscord-nitro.gift/abc": "LINK_BRAND_IN_DOMAIN",
            "https://steamcomunnity.com/tradeoffer": "LINK_LOOKALIKE",
            "https://dіscord.gift/claim": "LINK_HOMOGLYPH",
            "https://discord.com@evil.ru/x": "LINK_USERINFO",
            "https://grabify.link/ABC": "LINK_IP_LOGGER",
            "[https://discord.com/gift](https://evil.example/claim)": "LINK_MASKED_MISMATCH",
            "https://discord.com.verify-login.ru/x": "LINK_BRAND_SUBDOMAIN",
        }
        for text, code in cases.items():
            report = analyse_text(text)
            self.assertIn(code, report.codes(), text)
            self.assertGreaterEqual(report.score, 0.7, text)

    def test_ordinary_links_are_not_flagged(self):
        for text in ("https://discord.com/channels/1/2", "https://www.youtube.com/watch?v=x", "https://github.com/a/b",
                     "https://pineapple.com", "https://apply.com/jobs", "https://store.steampowered.com/app/1"):
            self.assertEqual(analyse_text(text).score, 0.0, text)

    def test_invites_deny_and_allow_lists(self):
        self.assertEqual(analyse_text("join discord.gg/abc123").invites, ["abc123"])
        self.assertEqual(analyse_text("join discord.gg/ours", own_invites={"ours"}).invites, [])
        self.assertIn("LINK_DENYLIST", analyse_text("https://bad.example/x", deny={"bad.example"}).codes())
        self.assertEqual(analyse_text("https://bit.ly/x", allow={"bit.ly"}).score, 0.0)

    def test_attachments_are_judged_by_metadata_only(self):
        self.assertIn("FILE_DOUBLE_EXTENSION", analyse_attachment("invoice.pdf.exe").codes)
        self.assertIn("FILE_BIDI_TRICK", analyse_attachment("photo‮gnp.exe").codes)
        self.assertIn("FILE_TYPE_MISMATCH", analyse_attachment("cat.png", 10, "application/x-msdownload").codes)
        self.assertIsNone(analyse_attachment("cat.png", 10, "image/png"))


class ClearanceAndLevelTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(tier_from_permissions(P.BAN_MEMBERS), Tier.SENIOR)
        self.assertEqual(resolve_tier(5, [7], 0, owner_id=5, role_tiers={}), Tier.OWNER)
        self.assertEqual(resolve_tier(6, [7], 0, owner_id=5, role_tiers={"7": 2}), Tier.MODERATOR)
        self.assertEqual(resolve_tier(6, [], P.ADMINISTRATOR, owner_id=5, role_tiers={}, quarantined=True), Tier.MEMBER)
        self.assertEqual(resolve_tier(6, [], P.ADMINISTRATOR, owner_id=5, role_tiers={}, derive=False), Tier.MEMBER)
        with self.assertRaises(Refused):
            check(Tier.MODERATOR, "ban")
        with self.assertRaises(Refused):
            check(Tier.MODERATOR, "timeout", target_tier=Tier.MODERATOR)
        with self.assertRaises(Refused):
            check(Tier.ADMIN, "warn", actor_id=1, target_id=1)
        check(Tier.SENIOR, "ban", target_tier=Tier.MEMBER)

    def test_levels_raise_hold_and_decay(self):
        s = ThreatState()
        self.assertIsNotNone(s.raise_to(Level.CRITICAL, T0, "x", auto_max=4))
        self.assertEqual(s.level, Level.SEVERE)                              # capped at auto_max
        self.assertIsNone(s.tick(T0 + 60, 1800))
        self.assertIsNotNone(s.tick(T0 + 1801, 1800))
        self.assertEqual(s.level, Level.HIGH)
        s.set_by_staff(Level.CRITICAL, T0 + 2000, by=9, reason="drill", hold_s=3600)
        self.assertIsNone(s.tick(T0 + 5000, 1800))                           # holding
        self.assertIsNotNone(s.tick(T0 + 5700, 1800))                        # hold over: steps down
        restored = ThreatState.from_dict(s.to_dict())
        self.assertEqual(restored.level, s.level)

    def test_config_is_validated_and_versioned(self):
        cfg = SecurityConfig()
        self.assertEqual(cfg["mode"], "shadow")
        with self.assertRaises(ValueError):
            cfg.with_value("joins.min_joins", 0)
        with self.assertRaises(ValueError):
            cfg.with_value("nope", 1)
        live = cfg.with_value("mode", "live")
        self.assertTrue(live.live)
        self.assertEqual(SecurityConfig({"joins.min_joins": "bad"})["joins.min_joins"], 8)     # bad stored value
        self.assertIn("mode", cfg.diff(live))
        self.assertEqual(cfg.with_list("links.deny", "Bad.Example", True)["links.deny"], ["bad.example"])
        self.assertTrue(all(code in REASONS for code in ("NUKE_BUDGET", "JOIN_COHORT", "MSG_COORDINATED")))
        self.assertIn("5 joins", explain("JOIN_SURGE", count=5, window="1 min", ratio=4.0))
        self.assertIn("?", explain("JOIN_SURGE"))                           # missing facts never crash


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = GuildEngine(1, SecurityConfig({"probation_role": 7}), bot_id=99)

    def feed(self, events):
        outs = [self.engine.observe(e) for e in events]
        return outs, [p for o in outs for p in o.plans], [s for o in outs for s in o.signals]

    def test_one_flooding_spammer_is_cleaned_up_without_raising_the_threat_level(self):
        events = [msg(i, 7, "BUY cheap followers at my shop now!!", T0 + i * 0.5, member_days=0.1) for i in range(9)]
        _, plans, signals = self.feed(events)
        kinds = [p.kind for p in plans]
        self.assertIn("delete_messages", kinds)
        self.assertIn("timeout", kinds)                                      # repeat offender: short timeout
        self.assertTrue(all(p.params.get("seconds", 0) <= 600 for p in plans if p.kind == "timeout"))
        self.assertEqual(self.engine.level, Level.NORMAL)
        self.assertTrue(any(s.detector == "duplicate" for s in signals))

    def test_birthday_wishes_from_many_members_are_not_spam(self):
        events = [msg(i, 100 + i, "Happy birthday Raditya!! have a great day", T0 + i * 3) for i in range(25)]
        _, plans, _ = self.feed(events)
        self.assertEqual(plans, [])
        self.assertEqual(self.engine.incidents, {})

    def test_coordinated_phishing_from_new_accounts(self):
        events = [msg(i, 200 + i, f"free nitro for everyone https://dlscord-gift.com/claim{i} hurry", T0 + i,
                      member_days=0.01) for i in range(6)]
        _, plans, signals = self.feed(events)
        self.assertTrue(any(s.detector == "coordinated" and s.family == "phishing" for s in signals))
        self.assertIn("ban", [p.kind for p in plans])                       # proposed, never automatic
        ban = next(p for p in plans if p.kind == "ban")
        self.assertEqual(ban.authority, "confirm")
        self.assertGreaterEqual(self.engine.level, Level.ELEVATED)

    def test_staff_floods_are_not_spam_but_staff_phishing_links_are_removed(self):
        _, plans, _ = self.feed([msg(i, 22, "announcement part", T0 + i * 0.2, tier=2) for i in range(12)])
        self.assertEqual(plans, [])
        _, plans, _ = self.feed([msg(50, 22, "https://grabify.link/abc", T0 + 30, tier=2)])
        self.assertEqual([p.kind for p in plans], ["delete_messages"])

    def test_relaxed_channels_tolerate_roleplay_battles(self):
        engine = GuildEngine(1, SecurityConfig({"channels.relaxed": [55]}), bot_id=99)
        outs = [engine.observe(msg(i, 7, "!attack the northern fort", T0 + i * 0.6, channel=55)) for i in range(10)]
        self.assertEqual([p for o in outs for p in o.plans], [])

    def test_join_cohort_gets_probation_and_raises_the_level(self):
        joins = [MemberJoin(1, 500 + j, T0 + j * 3, created_at=T0 - 3600 * 6, has_avatar=False,
                            name=f"freebie{1000 + j}") for j in range(14)]
        _, plans, signals = self.feed(joins)
        self.assertTrue(any(s.detector == "join_cohort" for s in signals))
        probation = {u for p in plans if p.kind == "probation" for u in p.targets}
        self.assertGreaterEqual(len(probation), 10)
        self.assertGreaterEqual(self.engine.level, Level.HIGH)
        self.assertTrue(any(p.kind == "pause_invites" and p.authority == "confirm" for p in plans))

    def test_an_onboarding_wave_of_established_accounts_is_not_a_cohort(self):
        import random
        rng = random.Random(3)
        joins = [MemberJoin(1, 700 + j, T0 + j * 20, created_at=T0 - rng.randint(100, 3000) * 86400, has_avatar=True,
                            name=rng.choice(["Ari", "bima_k", "Citra22", "DewaWiryawan", "eka.s", "Fajar"]) + str(j),
                            invite_code=None) for j in range(12)]
        _, plans, signals = self.feed(joins)
        self.assertFalse(any(s.detector == "join_cohort" for s in signals))
        self.assertEqual(plans, [])

    def test_mention_bomb_and_webhook_flood(self):
        _, plans, _ = self.feed([msg(1, 7, "hey", T0, member_days=0.5, user_mentions=20)])
        self.assertEqual({p.kind for p in plans}, {"delete_messages", "timeout"})
        hooks = [Message(1, 10, 900 + i, 0, T0 + 10 + i * 0.3, content="@everyone free", webhook_id=77,
                         author_bot=True, everyone_attempt=True) for i in range(6)]
        _, plans, _ = self.feed(hooks)
        self.assertIn("delete_webhook", [p.kind for p in plans])

    def test_dangerous_attachment(self):
        _, plans, _ = self.feed([msg(1, 7, "my resume", T0, attachments=(Attachment("cv.pdf.exe", 1000),))])
        self.assertEqual([p.kind for p in plans], ["delete_messages"])


class AntiNukeTests(unittest.TestCase):
    def setUp(self):
        self.engine = GuildEngine(1, SecurityConfig(), bot_id=99)

    def test_a_staff_account_deleting_channels_is_quarantined_and_its_additions_contained(self):
        e = self.engine
        e.observe(AuditEntry(1, "webhook_create", 42, T0, target_id=5001, actor_tier=2))
        e.observe(AuditEntry(1, "bot_add", 42, T0 + 1, target_id=6001, actor_tier=2))
        plans = []
        for k in range(3):
            plans += e.observe(AuditEntry(1, "channel_delete", 42, T0 + 2 + k, target_id=900 + k, actor_tier=2)).plans
        kinds = {p.kind: p for p in plans}
        self.assertEqual(kinds["quarantine"].authority, "auto")
        self.assertEqual(kinds["quarantine"].targets, [42])
        self.assertEqual(kinds["delete_webhook"].targets, [5001])
        self.assertEqual(kinds["kick_bot"].targets, [6001])
        self.assertIn(kinds["restore"].authority, ("approve2", "confirm"))
        self.assertGreaterEqual(e.level, Level.SEVERE)

    def test_rudhabot_itself_and_allowlisted_accounts_are_not_counted(self):
        e = self.engine
        for k in range(10):
            self.assertEqual(e.observe(AuditEntry(1, "ban", 99, T0 + k, target_id=k)).plans, [])
        e.exempt = lambda kind, ident, det: kind == "user" and ident == 43
        for k in range(10):
            self.assertEqual(e.observe(AuditEntry(1, "channel_delete", 43, T0 + k, target_id=k)).plans, [])

    def test_granting_everyone_administrator_trips_the_budget_at_once_and_is_undone(self):
        e = self.engine
        out = e.observe(AuditEntry(1, "role_update", 42, T0, target_id=1, actor_tier=4, perms_before=P.SEND_MESSAGES,
                                   perms_after=P.SEND_MESSAGES | P.ADMINISTRATOR, target_is_everyone=True))
        kinds = {p.kind for p in out.plans}
        self.assertIn("quarantine", kinds)
        revert = next(p for p in out.plans if p.kind == "revert_permissions")
        self.assertEqual((revert.authority, revert.targets), ("auto", [1]))
        self.assertTrue(revert.params["remove"] & P.ADMINISTRATOR)

    def test_lowering_verification_and_deleting_automod_are_flagged(self):
        e = self.engine
        out = e.observe(AuditEntry(1, "guild_update", 42, T0, actor_tier=4, before={"verification_level": 3},
                                   after={"verification_level": 0}))
        self.assertEqual(out.signals[0].detector, "config_weakened")
        out = e.observe(AuditEntry(1, "automod_rule_delete", 42, T0 + 1, target_id=3, actor_tier=4))
        self.assertTrue(any(s.detector == "automod_removed" for s in out.signals))

    def test_incidents_close_after_quiet_and_levels_decay(self):
        e = self.engine
        for k in range(3):
            e.observe(AuditEntry(1, "channel_delete", 42, T0 + k, target_id=900 + k, actor_tier=2))
        self.assertEqual(len(e.incidents), 1)
        out = e.observe(Tick(1, T0 + 4000))
        self.assertEqual(len(out.closed), 1)
        self.assertLess(e.level, Level.SEVERE)


class LockdownSnapshotPostureTests(unittest.TestCase):
    def setUp(self):
        self.guild = demo_guild().model()

    def test_lockdown_plan_restore_and_manual_report(self):
        plan = plan_lockdown(self.guild, "channels", [100], role_tiers={}, protected=[104])
        changed = {(c.channel_id, c.target_id) for c in plan.changes}
        self.assertIn((101, 1), changed)
        self.assertIn((103, 2), changed)                                     # Citizen's explicit allow neutralised
        self.assertNotIn(104, {c.channel_id for c in plan.changes})          # protected
        g = self.guild
        for c in plan.changes:
            g = apply_change(g, c)
        overlap = plan_lockdown(g, "channels", [101], locked_elsewhere=[101])
        self.assertEqual(overlap.changes, [])
        g2 = g.replace_channel(g.channel(102).with_overwrite(1, "role", P.SEND_MESSAGES, 0))     # a moderator edit
        restore, manual = plan_restore(g2, plan.changes)
        self.assertEqual(len(restore), len(plan.changes) - 1)
        self.assertTrue(any("roleplay-battles" in m for m in manual))

    def test_snapshot_diff_recreates_deleted_things_and_reverts_permissions(self):
        fake = demo_guild()
        before = capture(fake.model())
        fake.delete_channel(102)
        fake.delete_role(3)
        role = fake.model().role(4)
        from dataclasses import replace
        fake._model = fake.model().replace_role(replace(role, permissions=role.permissions | P.ADMINISTRATOR))
        plan = diff(before, fake.model())
        kinds = [s.kind for s in plan.steps]
        self.assertIn("create_role", kinds)
        self.assertIn("create_channel", kinds)
        self.assertIn("edit_role", kinds)
        self.assertTrue(any("can't be restored" in n for n in plan.notes))

    def test_posture_flags_real_problems(self):
        from dataclasses import replace
        g = replace(self.guild, mfa_level=0, automod_rules=0,
                    roles=tuple(replace(r, permissions=r.permissions | P.MENTION_EVERYONE) if r.id == 1 else r
                                for r in self.guild.roles))
        score, findings = audit(g, configured={"mode": "shadow"})
        titles = [f.title for f in findings]
        self.assertIn("@everyone has dangerous permissions", titles)
        self.assertIn("2FA isn't required for moderation", titles)
        self.assertLess(score, 60)
        self.assertEqual(findings[0].severity, "critical")


if __name__ == "__main__":
    unittest.main()
