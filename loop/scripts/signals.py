"""
signals.py — Discord signal card posting for #creative-signals and #competitor-watch.

Posts typed embed cards to Discord via webhook for real-time loop observability.

Channels:
  - #creative-signals (channel_id from config["discord"]["channel_id"])
  - #competitor-watch  (channel_id from config["discord"]["competitor_channel_id"])

Integration hooks (where each signal should be called from):
  - emit_loop_start      → orchestrator.py run_cycle() start
  - emit_winner          → analyze.py when score > 7.0
  - emit_fatigue         → analyze.py when CTR drop > 20% week-over-week
  - emit_kill            → deploy.py when pausing a loser
  - emit_scale           → deploy.py when scaling winner budget
  - emit_challenger_ready → gate.py after challenger is written, before deploy
  - emit_brief_complete  → orchestrator.py after research pipeline completes
  - emit_loop_error      → orchestrator.py in exception handler
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

SIGNALS_CHANNEL_ID = "1482849535473614848"


# ── Signal Types ────────────────────────────────────────────────────────────


class SignalType(Enum):
    # Original signals
    WINNER = "🟢"
    FATIGUE = "🔴"
    CHALLENGER_READY = "🟡"
    KILL = "🔴"
    SCALE = "🟢"
    BRIEF_COMPLETE = "🔵"
    LOOP_START = "⚙️"
    LOOP_ERROR = "💥"

    # Performance
    HOOK_RATE_SPIKE = "🔥"
    CPM_SURGE = "📈"
    ZERO_SPEND = "⚠️"
    DAILY_CAP_HIT = "💰"

    # Testing
    TEST_READY = "🔬"
    VARIANT_DIVERGING = "📊"
    ANGLE_EXHAUSTED = "😮‍💨"
    NEW_ANGLE_IDENTIFIED = "💡"

    # Research
    REDDIT_VERBATIM = "🗣️"
    COMPETITOR_NEW_AD = "👀"
    COMPETITOR_KILLED = "💀"
    TREND_DETECTED = "📰"

    # Loop Health
    MEMORY_MILESTONE = "🧠"
    LEARNING_CONSOLIDATION = "📝"
    LOOP_STREAK = "🔥"        # same emoji as HOOK_RATE_SPIKE, different type
    CYCLE_SUMMARY = "📋"

    # Creative
    HOOK_TYPE_RANKING = "🏆"
    ANGLE_ROTATION_DUE = "🔄"
    MODULAR_READY = "🧩"
    AWARENESS_SHIFT = "🎯"

    # Platform-Specific
    NATIVE_ADVERTORIAL_CTR = "📰"  # same emoji as TREND_DETECTED, different type
    PUSH_CLICK_ANOMALY = "🔔"
    PLATFORM_WINNER = "🏅"


# ── Channel Routing ────────────────────────────────────────────────────────
# Signals routed to #competitor-watch; everything else goes to #creative-signals.

COMPETITOR_SIGNALS = frozenset({
    SignalType.COMPETITOR_NEW_AD,
    SignalType.COMPETITOR_KILLED,
    SignalType.TREND_DETECTED,
    SignalType.NEW_ANGLE_IDENTIFIED,
})


# ── Colors ──────────────────────────────────────────────────────────────────

SIGNAL_COLORS = {
    # Original
    SignalType.WINNER: 0x00C851,
    SignalType.FATIGUE: 0xFF4444,
    SignalType.CHALLENGER_READY: 0xFFBB33,
    SignalType.KILL: 0xFF4444,
    SignalType.SCALE: 0x00C851,
    SignalType.BRIEF_COMPLETE: 0x0099CC,
    SignalType.LOOP_START: 0x9933CC,
    SignalType.LOOP_ERROR: 0xFF4444,

    # Performance
    SignalType.HOOK_RATE_SPIKE: 0xFF6600,
    SignalType.CPM_SURGE: 0xFF8800,
    SignalType.ZERO_SPEND: 0xFFBB33,
    SignalType.DAILY_CAP_HIT: 0x00C851,

    # Testing
    SignalType.TEST_READY: 0x0099CC,
    SignalType.VARIANT_DIVERGING: 0x0099CC,
    SignalType.ANGLE_EXHAUSTED: 0x888888,
    SignalType.NEW_ANGLE_IDENTIFIED: 0xFFBB33,

    # Research
    SignalType.REDDIT_VERBATIM: 0xFF4500,
    SignalType.COMPETITOR_NEW_AD: 0x9933CC,
    SignalType.COMPETITOR_KILLED: 0x555555,
    SignalType.TREND_DETECTED: 0x1DA1F2,

    # Loop Health
    SignalType.MEMORY_MILESTONE: 0x9933CC,
    SignalType.LEARNING_CONSOLIDATION: 0x0099CC,
    SignalType.LOOP_STREAK: 0x00C851,
    SignalType.CYCLE_SUMMARY: 0x0099CC,

    # Creative
    SignalType.HOOK_TYPE_RANKING: 0xFFBB33,
    SignalType.ANGLE_ROTATION_DUE: 0xFF8800,
    SignalType.MODULAR_READY: 0x00C851,
    SignalType.AWARENESS_SHIFT: 0x9933CC,

    # Platform-Specific
    # NATIVE_ADVERTORIAL_CTR color is dynamic (set in card builder)
    SignalType.NATIVE_ADVERTORIAL_CTR: 0x1DA1F2,
    SignalType.PUSH_CLICK_ANOMALY: 0xFF6600,
    SignalType.PLATFORM_WINNER: 0x00C851,
}


# ── Card Builders ───────────────────────────────────────────────────────────


def _build_winner_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hook_type = kw.get("hook_type", "—")
    hook_rate = kw.get("hook_rate", "—")
    ctr = kw.get("ctr", "—")
    cpa = kw.get("cpa", "—")
    score = kw.get("score", "—")
    offer_name = kw.get("offer_name", "—")
    platform = kw.get("platform", "—")

    desc = (
        f"**Ad:** {ad_name} — {hook_type} hook\n"
        f"**Hook Rate:** {hook_rate}% | **CTR:** {ctr}% | **CPA:** ${cpa}\n"
        f"**Score:** {score} / 10\n"
        f"**Offer:** {offer_name} | **Platform:** {platform}\n"
        f"**Recommended Action:** Scale budget 20% ↑"
    )
    return {"title": "🟢 WINNER DETECTED", "description": desc}


def _build_fatigue_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hook_type = kw.get("hook_type", "—")
    pct_drop = kw.get("pct_drop", "—")
    ctr_prev = kw.get("ctr_prev", "—")
    ctr_curr = kw.get("ctr_curr", "—")
    score = kw.get("score", "—")

    desc = (
        f"**Ad:** {ad_name} — {hook_type} hook\n"
        f"**CTR dropped** {pct_drop}% week-over-week ({ctr_prev}% → {ctr_curr}%)\n"
        f"**Score:** {score} / 10\n"
        f"**Recommended Action:** Kill or refresh hook"
    )
    return {"title": "🔴 FATIGUE ALERT", "description": desc}


def _build_challenger_ready_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hypothesis = kw.get("hypothesis", "—")
    angle = kw.get("angle", "—")
    hook_type = kw.get("hook_type", "—")
    awareness_stage = kw.get("awareness_stage", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**Hypothesis:** {hypothesis}\n"
        f"**Angle:** {angle} | **Hook Type:** {hook_type}\n"
        f"**Awareness Stage:** {awareness_stage}\n\n"
        f"React ✅ to deploy · ❌ to skip"
    )
    return {"title": "🟡 CHALLENGER READY", "description": desc}


def _build_kill_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    reason = kw.get("reason", "—")
    cpa = kw.get("cpa", "—")
    target_cpa = kw.get("target_cpa", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**Reason:** {reason}\n"
        f"**CPA at kill:** ${cpa} (target: ${target_cpa})\n"
        f"**Marcel rule:** 3x CPA → kill"
    )
    return {"title": "🔴 AD KILLED", "description": desc}


def _build_scale_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    new_budget = kw.get("new_budget", "—")
    trigger_reason = kw.get("trigger_reason", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**New budget:** ${new_budget}/day (+20%)\n"
        f"**Trigger:** {trigger_reason}"
    )
    return {"title": "🟢 SCALING AD", "description": desc}


def _build_brief_complete_card(**kw) -> dict:
    topic = kw.get("topic", "—")
    platform = kw.get("platform", "—")
    angle_count = kw.get("angle_count", "—")

    desc = (
        f"**Topic:** {topic}\n"
        f"**Platform:** {platform}\n"
        f"**Key angles identified:** {angle_count}\n"
        f"**Baseline seeded** → loop ready"
    )
    return {"title": "🔵 BRIEF COMPLETE", "description": desc}


def _build_loop_start_card(**kw) -> dict:
    cycle_number = kw.get("cycle_number", "—")
    offer_name = kw.get("offer_name", "—")

    desc = (
        f"**Cycle:** {cycle_number}\n"
        f"**Offer:** {offer_name}\n"
        f"**Steps:** harvest → analyze → generate → deploy"
    )
    return {"title": "⚙️ LOOP CYCLE STARTING", "description": desc}


def _build_loop_error_card(**kw) -> dict:
    step = kw.get("step", "—")
    error_message = kw.get("error_message", "—")
    cycle_number = kw.get("cycle_number", "—")

    desc = (
        f"**Step:** {step}\n"
        f"**Error:** {error_message}\n"
        f"**Cycle:** {cycle_number}"
    )
    return {"title": "💥 LOOP ERROR", "description": desc}


# ── New Card Builders ───────────────────────────────────────────────────────


def _build_hook_rate_spike_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    prev_rate = kw.get("prev_rate", "—")
    curr_rate = kw.get("curr_rate", "—")
    delta = kw.get("delta", "—")
    hypothesis = kw.get("hypothesis", "—")

    desc = (
        f"**Ad:** {ad_name}\n"
        f"**Hook Rate:** {prev_rate}% → {curr_rate}% (+{delta} pts overnight)\n"
        f"**Possible cause:** {hypothesis}\n"
        f"**Action:** Analyze what changed — duplicate this structure"
    )
    return {"title": "🔥 HOOK RATE SPIKE", "description": desc}


def _build_cpm_surge_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    prev_cpm = kw.get("prev_cpm", "—")
    curr_cpm = kw.get("curr_cpm", "—")
    pct_change = kw.get("pct_change", "—")

    desc = (
        f"**Ad:** {ad_name}\n"
        f"**CPM:** ${prev_cpm} → ${curr_cpm} (+{pct_change}% in 24h)\n"
        f"**Signal:** Audience saturation approaching\n"
        f"**Action:** Refresh creative before CTR drops"
    )
    return {"title": "📈 CPM SURGE WARNING", "description": desc}


def _build_zero_spend_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hours_live = kw.get("hours_live", "—")

    desc = (
        f"**Ad:** {ad_name}\n"
        f"**$0 spend** after {hours_live}h live\n"
        f"This is a **DELIVERY** issue, not a creative problem\n"
        f"**Action:** Check ad set budget, targeting, bid"
    )
    return {"title": "⚠️ ZERO SPEND ALERT", "description": desc}


def _build_daily_cap_hit_card(**kw) -> dict:
    campaign_name = kw.get("campaign_name", "Unknown Campaign")
    time_of_day = kw.get("time_of_day", "—")
    spend_so_far = kw.get("spend_so_far", "—")
    daily_budget = kw.get("daily_budget", "—")

    desc = (
        f"**Campaign:** {campaign_name}\n"
        f"**Cap hit at:** {time_of_day} (before noon = scale signal)\n"
        f"**Spend:** ${spend_so_far} of ${daily_budget}\n"
        f"**Action:** Consider increasing daily budget 20%"
    )
    return {"title": "💰 BUDGET CAP HIT EARLY", "description": desc}


def _build_test_ready_card(**kw) -> dict:
    variant_a = kw.get("variant_a", "—")
    variant_b = kw.get("variant_b", "—")
    ctr_a = kw.get("ctr_a", "—")
    ctr_b = kw.get("ctr_b", "—")
    confidence = kw.get("confidence", "—")
    winner_name = kw.get("winner_name", "—")

    desc = (
        f"**Variant A:** {variant_a} — CTR {ctr_a}%\n"
        f"**Variant B:** {variant_b} — CTR {ctr_b}%\n"
        f"**Confidence:** {confidence}%\n"
        f"**Winner:** {winner_name}\n"
        f"**Action:** Kill loser, scale winner"
    )
    return {"title": "🔬 TEST READY TO CALL", "description": desc}


def _build_variant_diverging_card(**kw) -> dict:
    variant_a = kw.get("variant_a", "—")
    variant_b = kw.get("variant_b", "—")
    ctr_gap = kw.get("ctr_gap", "—")
    estimated_spend = kw.get("estimated_spend", "—")
    loser_name = kw.get("loser_name", "—")

    desc = (
        f"**{variant_a}** vs **{variant_b}**\n"
        f"**CTR gap:** {ctr_gap}% (threshold: 25%)\n"
        f"Can call winner early — saving ${estimated_spend} in test spend\n"
        f"**Recommend:** Kill {loser_name} now"
    )
    return {"title": "📊 VARIANTS DIVERGING EARLY", "description": desc}


def _build_angle_exhausted_card(**kw) -> dict:
    angle_name = kw.get("angle_name", "—")
    variant_count = kw.get("variant_count", "—")
    best_cpa = kw.get("best_cpa", "—")
    target_cpa = kw.get("target_cpa", "—")

    desc = (
        f"**Angle:** {angle_name}\n"
        f"**Variants tested:** {variant_count}\n"
        f"**Best CPA achieved:** ${best_cpa} (target: ${target_cpa})\n"
        f"None beat baseline. Move to next angle."
    )
    return {"title": "😮‍💨 ANGLE EXHAUSTED", "description": desc}


def _build_new_angle_identified_card(**kw) -> dict:
    source = kw.get("source", "—")
    source_industry = kw.get("source_industry", "—")
    target_niche = kw.get("target_niche", "—")
    angle_description = kw.get("angle_description", "—")
    emotional_driver = kw.get("emotional_driver", "—")

    desc = (
        f"**Source:** {source} (cross-vertical research)\n"
        f"**Industry:** {source_industry} → applied to {target_niche}\n"
        f"**Angle:** {angle_description}\n"
        f"**Emotional driver:** {emotional_driver}\n"
        f"No competitor is running this yet in your category."
    )
    return {"title": "💡 NEW ANGLE IDENTIFIED", "description": desc}


def _build_reddit_verbatim_card(**kw) -> dict:
    subreddit = kw.get("subreddit", "—")
    upvotes = kw.get("upvotes", "—")
    verbatim_text = kw.get("verbatim_text", "—")
    hook_potential = kw.get("hook_potential", "—")

    desc = (
        f"**Subreddit:** r/{subreddit}\n"
        f"**Upvotes:** {upvotes}\n"
        f"**Quote:** \"{verbatim_text}\"\n"
        f"**Hook potential:** {hook_potential}/10\n"
        f"Use this exact language in next hook"
    )
    return {"title": "🗣️ REDDIT VERBATIM FOUND", "description": desc}


def _build_competitor_new_ad_card(**kw) -> dict:
    competitor_name = kw.get("competitor_name", "—")
    days_running = kw.get("days_running", "—")
    format_type = kw.get("format_type", "—")
    hook_type = kw.get("hook_type", "—")
    angle_summary = kw.get("angle_summary", "—")

    desc = (
        f"**Competitor:** {competitor_name}\n"
        f"**Running since:** {days_running} days\n"
        f"**Format:** {format_type} | **Hook type:** {hook_type}\n"
        f"**Angle:** {angle_summary}\n"
        f"**Action:** Study this — if still live in 7 days, it's working"
    )
    return {"title": "👀 COMPETITOR NEW AD DETECTED", "description": desc}


def _build_competitor_killed_card(**kw) -> dict:
    competitor_name = kw.get("competitor_name", "—")
    total_days = kw.get("total_days", "—")
    last_seen_date = kw.get("last_seen_date", "—")

    desc = (
        f"**Competitor:** {competitor_name}\n"
        f"**Ad ran for:** {total_days} days\n"
        f"**Last seen:** {last_seen_date}\n"
        f"**Signal:** They killed a loser (< 14 days) OR refreshed (> 30 days)"
    )
    return {"title": "💀 COMPETITOR AD WENT DARK", "description": desc}


def _build_trend_detected_card(**kw) -> dict:
    keyword = kw.get("keyword", "—")
    trend_index = kw.get("trend_index", "—")
    pct_change = kw.get("pct_change", "—")
    offer_name = kw.get("offer_name", "—")
    suggested_hook = kw.get("suggested_hook", "—")

    desc = (
        f"**Keyword:** \"{keyword}\"\n"
        f"**Trend index:** {trend_index} (up {pct_change}% this week)\n"
        f"**Relevance:** {offer_name}\n"
        f"**Window:** Act within 72h before CPMs spike\n"
        f"**Suggested hook:** \"{suggested_hook}\""
    )
    return {"title": "📰 TREND SPIKE DETECTED", "description": desc}


def _build_memory_milestone_card(**kw) -> dict:
    total_count = kw.get("total_count", "—")
    milestone = kw.get("milestone", "—")

    desc = (
        f"**Qdrant collection:** rachel-memories\n"
        f"**Patterns stored:** {total_count} ({milestone} milestone)\n"
        f"Cross-campaign knowledge compounding ✓"
    )
    return {"title": "🧠 MEMORY MILESTONE", "description": desc}


def _build_learning_consolidation_card(**kw) -> dict:
    new_word_count = kw.get("new_word_count", "—")
    top_insight = kw.get("top_insight", "—")

    desc = (
        f"**learnings.md** exceeded 2,000 words\n"
        f"**Compressed to** {new_word_count} words\n"
        f"**Key insight preserved:** \"{top_insight}\"\n"
        f"**Full history:** loop/learnings/archive/"
    )
    return {"title": "📝 LEARNINGS CONSOLIDATED", "description": desc}


def _build_loop_streak_card(**kw) -> dict:
    streak_count = kw.get("streak_count", "—")
    uptime_hours = kw.get("uptime_hours", "—")
    total_ads_tested = kw.get("total_ads_tested", "—")
    total_winners = kw.get("total_winners", "—")

    desc = (
        f"**{streak_count} consecutive cycles** with zero errors\n"
        f"**Uptime:** {uptime_hours}h\n"
        f"**Ads tested:** {total_ads_tested} | **Winners found:** {total_winners}"
    )
    return {"title": f"🔥 LOOP STREAK: {streak_count} CYCLES CLEAN", "description": desc}


def _build_cycle_summary_card(**kw) -> dict:
    week_start = kw.get("week_start", "—")
    week_end = kw.get("week_end", "—")
    cycle_count = kw.get("cycle_count", "—")
    ads_tested = kw.get("ads_tested", "—")
    winners = kw.get("winners", "—")
    killed = kw.get("killed", "—")
    best_cpa = kw.get("best_cpa", "—")
    best_ad_name = kw.get("best_ad_name", "—")
    worst_hook_type = kw.get("worst_hook_type", "—")
    worst_hook_ctr = kw.get("worst_hook_ctr", "—")
    top_angle = kw.get("top_angle", "—")

    desc = (
        f"**Week of** {week_start} → {week_end}\n"
        f"**Cycles run:** {cycle_count}\n"
        f"**Ads tested:** {ads_tested}\n"
        f"**Winners:** {winners} | **Killed:** {killed}\n"
        f"**Best CPA:** ${best_cpa} ({best_ad_name})\n"
        f"**Worst hook:** {worst_hook_type} ({worst_hook_ctr}% CTR avg)\n"
        f"**Top angle this week:** {top_angle}"
    )
    return {"title": "📋 WEEKLY CYCLE SUMMARY", "description": desc}


def _build_hook_type_ranking_card(**kw) -> dict:
    hooks = []
    for i in range(1, 6):
        name = kw.get(f"hook_{i}", "—")
        ctr = kw.get(f"ctr_{i}", "—")
        hooks.append(f"**{i}.** {name} — {ctr}% avg CTR")
    hook_1 = kw.get("hook_1", "—")

    desc = "\n".join(hooks) + f"\n\nRun more **{hook_1}** hooks this cycle"
    return {"title": "🏆 HOOK TYPE LEADERBOARD (this week)", "description": desc}


def _build_angle_rotation_due_card(**kw) -> dict:
    angle_name = kw.get("angle_name", "—")
    days_running = kw.get("days_running", "—")
    trend_direction = kw.get("trend_direction", "—")
    suggested_angle = kw.get("suggested_angle", "—")

    desc = (
        f"**Angle:** {angle_name}\n"
        f"**Running since:** {days_running} days (Marcel rule: 14 days max)\n"
        f"**Performance trend:** {trend_direction}\n"
        f"**Suggested next angle:** {suggested_angle}"
    )
    return {"title": "🔄 ANGLE ROTATION DUE", "description": desc}


def _build_modular_ready_card(**kw) -> dict:
    winner_name = kw.get("winner_name", "—")
    combo_count = kw.get("combo_count", "—")

    desc = (
        f"**Winner isolated:** {winner_name}\n"
        f"**Hook:** ✓ | **Body:** ✓ | **CTA:** ✓\n"
        f"**Possible combinations:** {combo_count} (5 hooks × 4 bodies × 3 CTAs)\n"
        f"Generate batch? React 🚀 to trigger variant generation"
    )
    return {"title": "🧩 MODULAR VARIANTS READY", "description": desc}


def _build_awareness_shift_card(**kw) -> dict:
    offer_name = kw.get("offer_name", "—")
    prev_stage = kw.get("prev_stage", "—")
    new_stage = kw.get("new_stage", "—")
    shift_signal = kw.get("shift_signal", "—")
    recommended_hooks = kw.get("recommended_hooks", "—")

    desc = (
        f"**Offer:** {offer_name}\n"
        f"**Previous stage:** {prev_stage} | **New stage:** {new_stage}\n"
        f"**Signal:** {shift_signal}\n"
        f"**Recommended hook style:** {recommended_hooks}\n"
        f"Update prompts.ads.yaml awareness_stage to {new_stage}"
    )
    return {"title": "🎯 AWARENESS STAGE SHIFT DETECTED", "description": desc}


def _build_native_advertorial_ctr_card(**kw) -> dict:
    advertorial_name = kw.get("advertorial_name", "—")
    platform = kw.get("platform", "—")
    ctr = kw.get("ctr", 0)
    action = kw.get("action", "—")

    try:
        ctr_val = float(ctr)
    except (ValueError, TypeError):
        ctr_val = 0

    status = "✅ TARGET HIT" if ctr_val >= 15 else "⚠️ BELOW TARGET"

    desc = (
        f"**Advertorial:** {advertorial_name}\n"
        f"**Platform:** {platform} (Outbrain/Taboola/Newsbreak)\n"
        f"**CTR:** {ctr}% (target: 15-18%)\n"
        f"**Status:** {status}\n"
        f"**Action:** {action}"
    )
    # Dynamic color based on CTR
    color = 0x1DA1F2 if ctr_val >= 15 else 0xFF8800
    return {"title": "📰 NATIVE ADVERTORIAL MILESTONE", "description": desc, "color_override": color}


def _build_push_click_anomaly_card(**kw) -> dict:
    creative_name = kw.get("creative_name", "—")
    click_rate = kw.get("click_rate", "—")
    multiplier = kw.get("multiplier", "—")
    send_volume = kw.get("send_volume", "—")

    desc = (
        f"**Creative:** {creative_name}\n"
        f"**Platform:** Pushnami\n"
        f"**Click rate:** {click_rate}% ({multiplier}x normal)\n"
        f"**Send volume:** {send_volume}\n"
        f"**Investigate:** What made this resonate?"
    )
    return {"title": "🔔 PUSH NOTIFICATION ANOMALY", "description": desc}


def _build_platform_winner_card(**kw) -> dict:
    concept_name = kw.get("concept_name", "—")
    platforms = kw.get("platforms", "—")
    meta_ctr = kw.get("meta_ctr", "—")
    native_ctr = kw.get("native_ctr", "—")

    desc = (
        f"**Ad concept:** {concept_name}\n"
        f"**Winning on:** {platforms}\n"
        f"**Meta CTR:** {meta_ctr}% | **Native CTR:** {native_ctr}%\n"
        f"**Signal:** Universal message — scale everywhere\n"
        f"**Action:** Replicate on all remaining platforms"
    )
    return {"title": "🏅 CROSS-PLATFORM WINNER", "description": desc}


CARD_BUILDERS = {
    # Original
    SignalType.WINNER: _build_winner_card,
    SignalType.FATIGUE: _build_fatigue_card,
    SignalType.CHALLENGER_READY: _build_challenger_ready_card,
    SignalType.KILL: _build_kill_card,
    SignalType.SCALE: _build_scale_card,
    SignalType.BRIEF_COMPLETE: _build_brief_complete_card,
    SignalType.LOOP_START: _build_loop_start_card,
    SignalType.LOOP_ERROR: _build_loop_error_card,

    # Performance
    SignalType.HOOK_RATE_SPIKE: _build_hook_rate_spike_card,
    SignalType.CPM_SURGE: _build_cpm_surge_card,
    SignalType.ZERO_SPEND: _build_zero_spend_card,
    SignalType.DAILY_CAP_HIT: _build_daily_cap_hit_card,

    # Testing
    SignalType.TEST_READY: _build_test_ready_card,
    SignalType.VARIANT_DIVERGING: _build_variant_diverging_card,
    SignalType.ANGLE_EXHAUSTED: _build_angle_exhausted_card,
    SignalType.NEW_ANGLE_IDENTIFIED: _build_new_angle_identified_card,

    # Research
    SignalType.REDDIT_VERBATIM: _build_reddit_verbatim_card,
    SignalType.COMPETITOR_NEW_AD: _build_competitor_new_ad_card,
    SignalType.COMPETITOR_KILLED: _build_competitor_killed_card,
    SignalType.TREND_DETECTED: _build_trend_detected_card,

    # Loop Health
    SignalType.MEMORY_MILESTONE: _build_memory_milestone_card,
    SignalType.LEARNING_CONSOLIDATION: _build_learning_consolidation_card,
    SignalType.LOOP_STREAK: _build_loop_streak_card,
    SignalType.CYCLE_SUMMARY: _build_cycle_summary_card,

    # Creative
    SignalType.HOOK_TYPE_RANKING: _build_hook_type_ranking_card,
    SignalType.ANGLE_ROTATION_DUE: _build_angle_rotation_due_card,
    SignalType.MODULAR_READY: _build_modular_ready_card,
    SignalType.AWARENESS_SHIFT: _build_awareness_shift_card,

    # Platform-Specific
    SignalType.NATIVE_ADVERTORIAL_CTR: _build_native_advertorial_ctr_card,
    SignalType.PUSH_CLICK_ANOMALY: _build_push_click_anomaly_card,
    SignalType.PLATFORM_WINNER: _build_platform_winner_card,
}


# ── Core Emit ───────────────────────────────────────────────────────────────


def _get_webhook_url(config: dict, signal_type: SignalType) -> str | None:
    """Resolve webhook URL based on signal routing.

    Competitor signals → config["discord"]["competitor_webhook_url"]
    All others        → config["discord"]["webhook_url"] (fallback: notifications.discord_webhook)
    """
    discord_cfg = config.get("discord", {})

    if signal_type in COMPETITOR_SIGNALS:
        url = discord_cfg.get("competitor_webhook_url")
        if url:
            return url
        # Fall back to main webhook if competitor webhook not configured
        print(f"[signals] ⚠️  No competitor webhook configured — falling back to main webhook for {signal_type.name}")

    url = discord_cfg.get("webhook_url")
    if url:
        return url
    return config.get("notifications", {}).get("discord_webhook")


def emit_signal(signal_type: SignalType, config: dict, **kwargs) -> dict | None:
    """
    Post a typed Discord embed card to the appropriate channel.

    Competitor signals → #competitor-watch
    All others         → #creative-signals

    Returns the Discord API response JSON on success, None on failure or missing config.
    """
    webhook_url = _get_webhook_url(config, signal_type)
    if not webhook_url:
        print(f"[signals] ⚠️  No Discord webhook URL configured — skipping {signal_type.name}")
        return None

    card = CARD_BUILDERS[signal_type](**kwargs)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Support dynamic color override from card builders (e.g., NATIVE_ADVERTORIAL_CTR)
    color = card.pop("color_override", None) or SIGNAL_COLORS[signal_type]

    embed = {
        "title": card["title"],
        "description": card["description"],
        "color": color,
        "footer": {"text": f"Rachel · AutoResearchClaw v1.1 · {timestamp}"},
    }

    payload = {"embeds": [embed]}

    # Append ?wait=true so Discord returns the message object (needed for reactions)
    post_url = webhook_url.rstrip("/")
    if "?" in post_url:
        post_url += "&wait=true"
    else:
        post_url += "?wait=true"

    try:
        resp = requests.post(post_url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[signals] ❌ Failed to post {signal_type.name}: {exc}")
        return None

    msg = resp.json()

    # CHALLENGER_READY: add approval reactions automatically
    if signal_type == SignalType.CHALLENGER_READY:
        _add_reactions(webhook_url, msg.get("id"), ["✅", "❌"])

    # MODULAR_READY: add rocket reaction for batch trigger
    if signal_type == SignalType.MODULAR_READY:
        _add_reactions(webhook_url, msg.get("id"), ["🚀"])

    return msg


def _add_reactions(webhook_url: str, message_id: str | None, emojis: list[str]):
    """Add reaction emojis to a webhook message via Discord API."""
    if not message_id:
        return

    # Webhook URLs look like: https://discord.com/api/webhooks/{id}/{token}
    # We can use the webhook token to add reactions to messages it posted
    for emoji in emojis:
        try:
            encoded = requests.utils.quote(emoji)
            url = f"{webhook_url.rstrip('/')}/messages/{message_id}/reactions/{encoded}/@me"
            requests.put(url, timeout=10)
        except requests.RequestException:
            pass  # Non-critical — card was already posted


# ── Convenience Functions ───────────────────────────────────────────────────
# Original signals

# TODO: emit_loop_start → call from orchestrator.py run_cycle() start


def emit_loop_start(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LOOP_START, config, **kwargs)


# TODO: emit_winner → call from analyze.py when score > 7.0


def emit_winner(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.WINNER, config, **kwargs)


# TODO: emit_fatigue → call from analyze.py when CTR drop > 20% week-over-week


def emit_fatigue(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.FATIGUE, config, **kwargs)


# TODO: emit_kill → call from deploy.py when pausing a loser


def emit_kill(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.KILL, config, **kwargs)


# TODO: emit_scale → call from deploy.py when scaling winner budget


def emit_scale(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.SCALE, config, **kwargs)


# TODO: emit_challenger_ready → call from gate.py after challenger is written, before deploy


def emit_challenger_ready(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.CHALLENGER_READY, config, **kwargs)


# TODO: emit_brief_complete → call from orchestrator.py after research pipeline completes


def emit_brief_complete(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.BRIEF_COMPLETE, config, **kwargs)


# TODO: emit_loop_error → call from orchestrator.py in exception handler


def emit_loop_error(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LOOP_ERROR, config, **kwargs)


# ── Performance signals ─────────────────────────────────────────────────────


def emit_hook_rate_spike(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.HOOK_RATE_SPIKE, config, **kwargs)


def emit_cpm_surge(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.CPM_SURGE, config, **kwargs)


def emit_zero_spend(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.ZERO_SPEND, config, **kwargs)


def emit_daily_cap_hit(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.DAILY_CAP_HIT, config, **kwargs)


# ── Testing signals ─────────────────────────────────────────────────────────


def emit_test_ready(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.TEST_READY, config, **kwargs)


def emit_variant_diverging(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.VARIANT_DIVERGING, config, **kwargs)


def emit_angle_exhausted(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.ANGLE_EXHAUSTED, config, **kwargs)


def emit_new_angle_identified(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.NEW_ANGLE_IDENTIFIED, config, **kwargs)


# ── Research signals ─────────────────────────────────────────────────────────


def emit_reddit_verbatim(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.REDDIT_VERBATIM, config, **kwargs)


def emit_competitor_new_ad(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.COMPETITOR_NEW_AD, config, **kwargs)


def emit_competitor_killed(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.COMPETITOR_KILLED, config, **kwargs)


def emit_trend_detected(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.TREND_DETECTED, config, **kwargs)


# ── Loop Health signals ──────────────────────────────────────────────────────


def emit_memory_milestone(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.MEMORY_MILESTONE, config, **kwargs)


def emit_learning_consolidation(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LEARNING_CONSOLIDATION, config, **kwargs)


def emit_loop_streak(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LOOP_STREAK, config, **kwargs)


def emit_cycle_summary(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.CYCLE_SUMMARY, config, **kwargs)


# ── Creative signals ─────────────────────────────────────────────────────────


def emit_hook_type_ranking(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.HOOK_TYPE_RANKING, config, **kwargs)


def emit_angle_rotation_due(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.ANGLE_ROTATION_DUE, config, **kwargs)


def emit_modular_ready(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.MODULAR_READY, config, **kwargs)


def emit_awareness_shift(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.AWARENESS_SHIFT, config, **kwargs)


# ── Platform-Specific signals ────────────────────────────────────────────────


def emit_native_advertorial_ctr(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.NATIVE_ADVERTORIAL_CTR, config, **kwargs)


def emit_push_click_anomaly(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.PUSH_CLICK_ANOMALY, config, **kwargs)


def emit_platform_winner(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.PLATFORM_WINNER, config, **kwargs)


# ── CLI Usage ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Send a test signal to Discord")
    parser.add_argument("signal", choices=[s.name.lower() for s in SignalType], help="Signal type")
    args = parser.parse_args()

    cfg = yaml.safe_load(open(CONFIG_PATH))
    sig = SignalType[args.signal.upper()]
    result = emit_signal(sig, cfg, ad_name="Test Ad", cycle_number=0, step="test")

    if result:
        print(f"[signals] ✅ {sig.name} posted (message_id={result.get('id')})")
    else:
        print(f"[signals] ⚠️  {sig.name} not posted — check webhook config")
