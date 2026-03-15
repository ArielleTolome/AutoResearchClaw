# AutoResearchClaw — Creative Loop (v0.9.6)

Self-improving ad creative testing pipeline inspired by Karpathy's AutoResearch loop.

## The Loop

```
HARVEST → ANALYZE → GENERATE → DEPLOY → wait 24-48h → repeat
```

1. **Harvest** — Pull Meta ad performance (hook rate, CTR, CVR, CPA)
2. **Analyze** — Score ads, apply kill rules, log learnings via Claude
3. **Generate** — Write new challenger using learnings + creative frameworks
4. **Deploy** — Create challenger on Meta, pause losers, ping Slack

## Quick Start

```bash
cd loop/
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml
# fill in Meta + Anthropic keys

# Dry run (mock data)
python orchestrator/orchestrator.py --dry-run

# Live
python orchestrator/orchestrator.py --adset-id act_XXX --link-url https://yourpage.com
```

## Single Step

```bash
python orchestrator/orchestrator.py --step harvest
python orchestrator/orchestrator.py --step analyze
python orchestrator/orchestrator.py --step generate
python orchestrator/orchestrator.py --step deploy --adset-id act_XXX
```

## Scheduling

See `.github/workflows/loop.yml` — runs via GitHub Actions cron every 48h.
Add secrets: META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_ADSET_ID, META_PAGE_ID,
META_IMAGE_HASH, OFFER_LINK_URL, ANTHROPIC_API_KEY, OFFER_NAME, OFFER_NICHE,
TARGET_CPA, SLACK_WEBHOOK_URL

## Metrics

| Metric | Kill if... | Unicorn if... |
|--------|-----------|----------------|
| Hook Rate | < 15% | > 40% |
| CTR | < 0.5% | > 2% |
| CPA | > 3x target | < target |

## Learnings

`learnings/learnings.md` grows each cycle. The generator reads it before writing
new challengers — this is the compounding intelligence layer.
Run the consolidation prompt when it exceeds ~2000 words.

## Connection to AutoResearchClaw Core

The research pipeline (full 23-stage) feeds the loop:
- Run a brief on your offer via `research.sh` → get competitor intel, audience language, angles
- Seed `config/baseline.md` with the brief output
- Start the loop

The loop then autonomously tests and improves on what the research found.
