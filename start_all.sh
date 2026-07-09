#!/bin/bash
# Start the entire Synapse stack in the background
cd ~/Desktop/synapse
source .venv/bin/activate

mkdir -p logs

# Kill any old copies first (clean slate)
pkill -f "ingestion_server.py" 2>/dev/null
pkill -f "distance_tracker.py run" 2>/dev/null
pkill -f "study_tracker.py run" 2>/dev/null
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

sleep 3
echo ""
echo "Fleet status:"
curl -s http://localhost:5000/agents | python -m json.tool
echo ""
echo "🎉 Synapse is running! Safe to close this terminal."
echo "   Chat:  python smart_chat.py"
echo "   Stop:  ./stop_all.sh"
