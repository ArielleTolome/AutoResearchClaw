#!/usr/bin/env python3
"""
demo_signals.py — Fire one of every signal type to showcase v1.6.0

Usage:
  python demo_signals.py <creative_webhook_url> <competitor_webhook_url>

Fires a realistic sample card for each of the 32 signal types.
"""

import sys
import time
from signals import (
    emit_winner, emit_fatigue, emit_kill, emit_scale, emit_challenger_ready,
    emit_loop_start, emit_brief_complete, emit_loop_error,
    emit_hook_rate_spike, emit_cpm_surge, emit_zero_spend, emit_daily_cap_hit,
    emit_test_ready, emit_variant_diverging, emit_angle_exhausted, emit_new_angle_identified,
    emit_reddit_verbatim, emit_competitor_new_ad, emit_competitor_killed, emit_trend_detected,
    emit_memory_milestone, emit_learning_consolidation, emit_loop_streak, emit_cycle_summary,
    emit_hook_type_ranking, emit_angle_rotation_due, emit_modular_ready, emit_awareness_shift,
    emit_native_advertorial_ctr, emit_push_click_anomaly, emit_platform_winner,
)

if len(sys.argv) < 3:
    print("Usage: python demo_signals.py <creative_webhook_url> <competitor_webhook_url>")
    sys.exit(1)

creative_wh = sys.argv[1]
competitor_wh = sys.argv[2]

# Config stubs pointing to webhooks
creative_config = {
    "discord": {
        "webhook_url": creative_wh,
        "channel_id": "1482849535473614848",
        "competitor_webhook_url": competitor_wh,
        "competitor_channel_id": "1482851982837547048",
    }
}

print("🚀 Firing demo signals to #creative-signals and #competitor-watch...\n")

# ── Creative Signals ──────────────────────────────────────────────────────────

print("1/32 LOOP_START"); emit_loop_start(creative_config, cycle_number=42, offer_name="Auto Insurance", platform="meta"); time.sleep(0.8)

print("2/32 WINNER"); emit_winner(creative_config,
    ad_name="Renters Stay Alert — Demographics hook",
    hook_rate=38, ctr=2.1, cpa=18.40, score=8.4,
    offer="Auto Insurance", platform="Meta",
    action="Scale budget 20% ↑"
); time.sleep(0.8)

print("3/32 FATIGUE"); emit_fatigue(creative_config,
    ad_name="Millionaire Lets Worker Pick — Emotion hook",
    prev_ctr=2.3, curr_ctr=1.7, pct_drop=26,
    weeks_running=3, score=3.1,
    action="Kill or refresh hook"
); time.sleep(0.8)

print("4/32 HOOK_RATE_SPIKE"); emit_hook_rate_spike(creative_config,
    ad_name="Neighbor Discovery — Greed hook",
    prev_rate=24, curr_rate=38, delta=14,
    hypothesis="New visual thumbnail resonating with 35-44 demo"
); time.sleep(0.8)

print("5/32 CPM_SURGE"); emit_cpm_surge(creative_config,
    ad_name="Rate Hike Outrage — Anger hook",
    prev_cpm=8.20, curr_cpm=12.80, pct_change=56
); time.sleep(0.8)

print("6/32 ZERO_SPEND"); emit_zero_spend(creative_config,
    ad_name="Loyalty Tax Reveal — Pain hook",
    hours_live=8
); time.sleep(0.8)

print("7/32 DAILY_CAP_HIT"); emit_daily_cap_hit(creative_config,
    campaign_name="Auto Insurance — Cold Traffic",
    time_of_day="10:47 AM",
    spend_so_far=280, daily_budget=300
); time.sleep(0.8)

print("8/32 TEST_READY"); emit_test_ready(creative_config,
    variant_a="Greed Hook v1", ctr_a=2.4,
    variant_b="Fear Hook v1", ctr_b=1.6,
    confidence=95, winner_name="Greed Hook v1"
); time.sleep(0.8)

print("9/32 VARIANT_DIVERGING"); emit_variant_diverging(creative_config,
    variant_a="Cliffhanger Hook v2", variant_b="Question Hook v1",
    ctr_gap=31, estimated_spend=140, loser_name="Question Hook v1"
); time.sleep(0.8)

print("10/32 ANGLE_EXHAUSTED"); emit_angle_exhausted(creative_config,
    angle_name="Social Proof", variant_count=6,
    best_cpa=42.80, target_cpa=22.00
); time.sleep(0.8)

print("11/32 REDDIT_VERBATIM"); emit_reddit_verbatim(creative_config,
    subreddit="AutoInsurance",
    upvotes=847,
    verbatim_text="My rate went up $60/month and I never filed a single claim. Been with them 9 years.",
    hook_potential=9
); time.sleep(0.8)

print("12/32 MEMORY_MILESTONE"); emit_memory_milestone(creative_config,
    total_count=100, milestone="100"
); time.sleep(0.8)

print("13/32 LEARNING_CONSOLIDATION"); emit_learning_consolidation(creative_config,
    new_word_count=820,
    top_insight="Greed hooks outperform fear hooks 2:1 on auto insurance cold traffic"
); time.sleep(0.8)

print("14/32 LOOP_STREAK"); emit_loop_streak(creative_config,
    streak_count=12, uptime_hours=576,
    total_ads_tested=47, total_winners=8
); time.sleep(0.8)

print("15/32 CYCLE_SUMMARY"); emit_cycle_summary(creative_config,
    week_start="Mar 9", week_end="Mar 15",
    cycle_count=7, ads_tested=14, winners=3, killed=8,
    best_cpa=18.40, best_ad_name="Renters Stay Alert",
    worst_hook_type="Question", worst_hook_ctr=0.8,
    top_angle="Loyalty Betrayal"
); time.sleep(0.8)

print("16/32 HOOK_TYPE_RANKING"); emit_hook_type_ranking(creative_config,
    rankings=[
        ("Greed", 2.4), ("Cliffhanger", 2.1), ("Emotion", 1.9),
        ("Demographics", 1.7), ("Question", 0.8)
    ]
); time.sleep(0.8)

print("17/32 ANGLE_ROTATION_DUE"); emit_angle_rotation_due(creative_config,
    angle_name="Loyalty Betrayal",
    days_running=16, trend_direction="declining",
    suggested_angle="Mechanism / How-It-Works"
); time.sleep(0.8)

print("18/32 MODULAR_READY"); emit_modular_ready(creative_config,
    winner_name="Renters Stay Alert",
    combo_count=60
); time.sleep(0.8)

print("19/32 AWARENESS_SHIFT"); emit_awareness_shift(creative_config,
    offer_name="Auto Insurance",
    prev_stage=2, new_stage=3,
    shift_signal="Competitors flooding Stage 2; audience recognizes solutions now",
    recommended_hooks="Mechanism hooks, Comparison hooks, 'Why X beats Y' angles"
); time.sleep(0.8)

print("20/32 NATIVE_ADVERTORIAL_CTR"); emit_native_advertorial_ctr(creative_config,
    advertorial_name="The Loyalty Tax — Editorial style",
    platform="Outbrain", ctr=17.2
); time.sleep(0.8)

print("21/32 PUSH_CLICK_ANOMALY"); emit_push_click_anomaly(creative_config,
    creative_name="Rate Check Alert — Urgency push",
    click_rate=4.8, multiplier=3.2, send_volume=142000
); time.sleep(0.8)

print("22/32 PLATFORM_WINNER"); emit_platform_winner(creative_config,
    concept_name="Loyalty Betrayal — Neighbor Discovery",
    platforms=["Meta", "Outbrain", "Newsbreak"],
    meta_ctr=2.1, native_ctr=18.4
); time.sleep(0.8)

print("23/32 CHALLENGER_READY"); emit_challenger_ready(creative_config,
    ad_name="Loyalty Tax Cliffhanger v3",
    hook_type="Cliffhanger",
    based_on="Renters Stay Alert structure + 1★ review verbatim body",
    prediction_score=21
); time.sleep(0.8)

print("24/32 SCALE"); emit_scale(creative_config,
    ad_name="Renters Stay Alert",
    budget_increase=20, new_daily_budget=360
); time.sleep(0.8)

print("25/32 KILL"); emit_kill(creative_config,
    ad_name="Question Hook v1",
    cpa=67.20, target_cpa=22.00, reason="3x CPA target — Marcel kill rule"
); time.sleep(0.8)

print("26/32 BRIEF_COMPLETE"); emit_brief_complete(creative_config,
    topic="Auto Insurance — Loyalty Betrayal angle",
    platform="meta", sources_used=6,
    hooks_generated=30, angles_identified=3
); time.sleep(0.8)

# ── Competitor Watch signals ──────────────────────────────────────────────────

print("27/32 COMPETITOR_NEW_AD (→ #competitor-watch)"); emit_competitor_new_ad(creative_config,
    competitor_name="The Zebra",
    days_running=9, format_type="UGC video (30s)",
    hook_type="Testimonial / Social proof",
    angle_summary="Real customer saved $800 switching — shows phone screen with new rate"
); time.sleep(0.8)

print("28/32 COMPETITOR_KILLED (→ #competitor-watch)"); emit_competitor_killed(creative_config,
    competitor_name="Insurify",
    total_days=11, last_seen_date="Mar 14"
); time.sleep(0.8)

print("29/32 TREND_DETECTED (→ #competitor-watch)"); emit_trend_detected(creative_config,
    keyword="car insurance rate increase 2026",
    trend_index=89, pct_change=340,
    offer_name="Auto Insurance",
    suggested_hook="Your car insurance just went up — here's why (and how to fight it)"
); time.sleep(0.8)

print("30/32 NEW_ANGLE_IDENTIFIED (→ #competitor-watch)"); emit_new_angle_identified(creative_config,
    source="Cross-vertical research — Health Insurance industry",
    source_industry="Health Insurance",
    target_niche="Auto Insurance",
    angle_description="Annual checkup framing: 'When did you last audit your auto rate?'",
    emotional_driver="Proactive self-improvement / FOMO on savings"
); time.sleep(0.8)

print("31/32 LOOP_ERROR (recovery test)"); emit_loop_error(creative_config,
    step="HARVEST", error_msg="Tavily API rate limit hit — retrying in 60s",
    cycle_number=42
); time.sleep(0.8)

print("32/32 LOOP_START (next cycle)"); emit_loop_start(creative_config,
    cycle_number=43, offer_name="Auto Insurance", platform="meta"
); time.sleep(0.5)

print("\n✅ All 32 signals fired!")
print("   #creative-signals → 28 cards")
print("   #competitor-watch → 4 cards")
print("\nCheck Discord 👀")
