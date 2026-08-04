#!/bin/bash
pkill -f "ingestion_server.py"
pkill -f "distance_tracker.py run"
pkill -f "study_tracker.py run"
pkill -f "water_tracker.py run"
pkill -f "job_application_tracker.py run"
pkill -f "telegram_bot.py"
echo "🛑 Synapse stopped."
