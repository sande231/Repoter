"""
Synapse Chat - Talk to your agent fleet.

Usage:
    python chat.py

Commands:
    status                     → fleet overview (all agents, health)
    log <km> <mode>            → log distance (e.g., "log 5 walking")
    today                      → today's distance totals
    agents                     → list registered agents
    metrics <agent_id>         → recent metrics for an agent
    help                       → show commands
    quit / exit                → leave
"""

import time
import requests
from distance_tracker import DistanceTracker, VALID_MODES

INGESTION_URL = "http://localhost:5000"


class SynapseChat:
    """Command-based chatbot for the Synapse fleet. (LLM brain plugs in at Step 3.)"""

    def __init__(self):
        self.tracker = DistanceTracker()

    # ---------- Fleet queries ----------

    def get_agents(self):
        try:
            resp = requests.get(f"{INGESTION_URL}/agents", timeout=3)
            return resp.json() if resp.ok else None
        except Exception:
            return None

    def cmd_status(self) -> str:
        agents = self.get_agents()
        if agents is None:
            return "🔴 Can't reach the ingestion server. Is it running on port 5000?"
        if not agents:
            return "🟡 Server is up, but no agents are registered yet."

        now = int(time.time())
        lines = [f"🟢 Fleet status: {len(agents)} agent(s) registered\n"]
        for a in agents:
            agent_id = a.get("agent_id", "?")
            last_seen = a.get("info", {}).get("last_seen", 0)
            age = now - int(last_seen)
            if age < 120:
                health = f"🟢 active ({age}s ago)"
            elif age < 600:
                health = f"🟡 quiet ({age // 60}m ago)"
            else:
                health = f"🔴 STALE ({age // 60}m ago)"
            name = a.get("info", {}).get("info", {}).get("name", agent_id)
            lines.append(f"  • {name} ({agent_id}) — {health}")
        return "\n".join(lines)

    def cmd_agents(self) -> str:
        agents = self.get_agents()
        if agents is None:
            return "🔴 Can't reach the ingestion server."
        return "\n".join(f"  • {a['agent_id']}" for a in agents) or "No agents registered."

    def cmd_metrics(self, agent_id: str) -> str:
        try:
            since = int(time.time()) - 3600
            resp = requests.get(
                f"{INGESTION_URL}/metrics/{agent_id}",
                params={"since": since}, timeout=3,
            )
            data = resp.json() if resp.ok else []
        except Exception:
            return "🔴 Can't reach the ingestion server."

        if not data:
            return f"No metrics for '{agent_id}' in the last hour."

        latest = data[-1].get("metrics", {})
        lines = [f"📊 Latest metrics for {agent_id} ({len(data)} entries last hour):"]
        for k, v in latest.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ---------- Distance commands ----------

    def cmd_log(self, km_str: str, mode: str) -> str:
        try:
            totals = self.tracker.log_distance(float(km_str), mode)
        except ValueError as e:
            return f"❌ {e}"
        by_mode = ", ".join(f"{v} {k}" for k, v in totals["by_mode"].items())
        return f"✅ Logged {km_str} km {mode}!\n📊 Today: {totals['total_km']} km total ({by_mode})"

    def cmd_today(self) -> str:
        totals = self.tracker.get_today_totals()
        if totals["entries"] == 0:
            return "📊 No distance logged today yet. Try: log 5 walking"
        lines = [f"📊 Today ({totals['date']}): {totals['total_km']} km total"]
        for mode, km in totals["by_mode"].items():
            lines.append(f"  • {mode}: {km} km")
        return "\n".join(lines)

    # ---------- Command router ----------

    def handle(self, user_input: str) -> str:
        parts = user_input.strip().split()
        if not parts:
            return ""
        cmd = parts[0].lower()

        if cmd == "status":
            return self.cmd_status()
        if cmd == "agents":
            return self.cmd_agents()
        if cmd == "metrics" and len(parts) == 2:
            return self.cmd_metrics(parts[1])
        if cmd == "log" and len(parts) == 3:
            return self.cmd_log(parts[1], parts[2])
        if cmd == "today":
            return self.cmd_today()
        if cmd == "help":
            return __doc__
        return f"🤔 Unknown command: '{user_input}'. Type 'help' for commands."


def main():
    chat = SynapseChat()
    print("=" * 55)
    print("  💬 SYNAPSE CHAT — talk to your agent fleet")
    print("  Type 'help' for commands, 'quit' to exit")
    print("=" * 55)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Bye!")
            break
        if user_input.lower() in {"quit", "exit", "bye"}:
            print("👋 Bye!")
            break
        response = chat.handle(user_input)
        if response:
            print(f"\nBot: {response}")


if __name__ == "__main__":
    main()