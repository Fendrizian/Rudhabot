"""How Clairvoyance learns this server from staff verdicts (no new weights): a mixin of SecurityService.

Every alert has Real / False alarm buttons. Verdicts are kept in SQLite and feed per-detector precision (status,
reports, the health check) and the confidence bar the message and incident models must clear here: 0.80 normally,
rising to at most 0.95 when alerts a model contributed to are mostly judged false alarms.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rudhabot.security.clearance import Refused

if TYPE_CHECKING:                          # annotations only: the service module imports this one
    from rudhabot.services.security import Runtime

MODEL_BAR, MODEL_BAR_MAX = 0.80, 0.95      # the confidence a model reading needs; staff verdicts move it within these


class LearningMixin:
    # ================================================================ staff verdicts: Clairvoyance learns this server
    async def verdict(self, guild_id: int, incident_id: str, user_id: int, real: bool) -> str:
        """A staff member's judgement of an alert. It feeds per-detector precision (shown in status, reports and the
        health check) and the per-server confidence bar the message and incident models must clear."""
        self.require(guild_id, user_id, "view_incidents")
        rt = self.runtime(guild_id)
        inc = self._incident(rt, incident_id)
        if inc is None:
            raise Refused("No such incident.")
        detectors = sorted(set(inc.counts))
        model = bool(inc.model_bonus) or any(s.get("source") == "model" for s in inc.signals)
        self.store.set_verdict(guild_id, incident_id, "real" if real else "false", user_id, inc.family, detectors, model)
        inc.note(self.now(), "verdict", f"<@{user_id}> marked it {'a real problem' if real else 'a false alarm'}")
        if inc.id in rt.engine.incidents:
            rt.dirty.add(inc.id)
        else:
            self.store.save_incident(guild_id, inc.to_dict())
        cal = self._apply_calibration(rt)
        return (f"Thanks: recorded as {'real' if real else 'a false alarm'}. Clairvoyance has {cal['verdicts']} "
                f"verdict(s) here; its model bar is now {cal['message_bar']:.2f}.")

    def calibration(self, guild_id: int) -> dict:
        """Precision of alerts by detector, and the confidence bars the models must clear on this server: they rise
        (up to 0.95) when alerts involving a model are judged false alarms, and fall back to 0.80 as they're judged
        real. Needs 5 model-involved verdicts before moving at all."""
        verdicts = self.store.verdicts(guild_id, 200)
        real = [v for v in verdicts if v["verdict"] == "real"]
        per = {}
        for v in verdicts:
            for d in v["detectors"]:
                p = per.setdefault(d, [0, 0])
                p[0] += v["verdict"] == "real"
                p[1] += 1
        model = [v for v in verdicts if v["model"]][:60]
        bar = MODEL_BAR
        if len(model) >= 5:
            false_share = sum(1 for v in model if v["verdict"] == "false") / len(model)
            bar = min(MODEL_BAR_MAX, MODEL_BAR + (MODEL_BAR_MAX - MODEL_BAR) * max(0.0, false_share - 0.3) / 0.7)
        return {"verdicts": len(verdicts), "precision": len(real) / len(verdicts) if verdicts else 0.0,
                "detectors": {d: {"real": r, "total": t, "precision": r / t} for d, (r, t) in per.items()},
                "model_verdicts": len(model), "message_bar": round(bar, 3), "incident_bar": round(bar, 3)}

    def _apply_calibration(self, rt: "Runtime") -> dict:
        cal = self.calibration(rt.guild_id)
        rt.engine.message_bar, rt.engine.incident_bar = cal["message_bar"], cal["incident_bar"]
        return cal


#: what to adjust when staff verdicts say a detector is noisy on this server
DETECTOR_HINTS = {
    "flood": "Raise messages.flood_count, or mark busy game channels relaxed (channels.relaxed).",
    "duplicate": "Raise messages.dup_count, or mark channels with repeated commands relaxed.",
    "coordinated": "Raise messages.coord_accounts.",
    "slow_drip": "Raise messages.drip_accounts, or allow the site (links.allow) if new members share it legitimately.",
    "channel_surge": "Mark event/announcement channels relaxed or protected.",
    "join_surge": "Raise joins.min_joins or joins.surge_ratio (common around events and partner posts).",
    "join_cohort": "Raise joins.cohort_min.",
    "invite": "Set messages.invites to allow if invites are normal here.",
    "link": "Add trusted sites to links.allow.",
    "model_scam": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.message off.",
    "model_spam": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.message off.",
    "incident_model": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.incident off.",
}
