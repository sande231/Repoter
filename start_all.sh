#!/bin/bash
# Start the entire Synapse stack in the background
cd ~/Desktop/synapse
source .venv/bin/activate

# Load .env into this shell so every process started below (ingestion server, trackers)
# actually sees INGESTION_API_KEY, DASHBOARD_*, ANTHROPIC_API_KEY, etc. Without this,
# they silently ran with none of it set - auth checks either failed closed or, worse,
# fell back to no-auth-required.
# NOTE: plain "source .env" mis-parses any value containing spaces (e.g. the Gmail app
# password) - bash word-splits it and tries to run the leftover words as commands. Export
# each line as a single quoted argument instead so values with spaces survive intact.
set -a
while IFS= read -r line || [ -n "$line" ]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  export "$line"
done < .env
set +a

mkdir -p logs

# Kill any old copies first (clean slate)
pkill -f "ingestion_server.py" 2>/dev/null
pkill -f "distance_tracker.py run" 2>/dev/null
pkill -f "study_tracker.py run" 2>/dev/null
pkill -f "water_tracker.py run" 2>/dev/null
pkill -f "job_application_tracker.py run" 2>/dev/null
pkill -f "telegram_bot.py" 2>/dev/null
sleep 1

# Start server
nohup python ingestion_server.py > logs/server.log 2>&1 &
echo "✅ Ingestion server starting (logs/server.log)"
sleep 2

# Start agents
nohup python distance_tracker.py run > logs/distance_tracker.log 2>&1 &
echo "✅ Distance tracker starting"

nohup python study_tracker.py run > logs/study_tracker.log 2>&1 &
echo "✅ Study tracker starting"

nohup python water_tracker.py run > logs/water_tracker.log 2>&1 &
echo "✅ Water tracker starting"

nohup python job_application_tracker.py run > logs/job_application_tracker.log 2>&1 &
echo "✅ Job application tracker starting"

nohup python -u telegram_bot.py > logs/telegram_bot.log 2>&1 &
echo "✅ Telegram bot starting"

sleep 3
echo ""
echo "Fleet status:"
curl -s -H "X-API-KEY: ${INGESTION_API_KEY}" http://localhost:5000/agents | python -m json.tool
echo ""
echo "🎉 Synapse is running! Safe to close this terminal."
echo "   Chat:  python smart_chat.py"
echo "   Stop:  ./stop_all.sh"
