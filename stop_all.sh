cat > ~/Desktop/synapse/stop_all.sh << 'EOF'
#!/bin/bash
pkill -f "ingestion_server.py"
pkill -f "distance_tracker.py run"
pkill -f "study_tracker.py run"
echo "🛑 Synapse stopped."
EOF
chmod +x ~/Desktop/synapse/stop_all.sh