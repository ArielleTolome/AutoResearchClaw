#!/bin/bash
cd "$(dirname "$0")/../.."
source venv/bin/activate
python3 loop/bot/discord_bot.py
