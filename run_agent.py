"""
Run any agent with self-healing enabled via the universal healing wrapper.
"""

import asyncio
from canvas_tutor_adapter import CanvasTutorAdapter
from healing_wrapper import make_healable, run_with_healing


def main():
    # 1. Create your agent (any agent works)
    adapter = CanvasTutorAdapter()

    # 2. Wrap it to make it healable
    agent = make_healable(adapter, agent_id=adapter.agent_id)

    # 3. Run with healing in the background
    asyncio.run(
        run_with_healing(
            agent,
            work_fn=adapter.publish_heartbeat,
            work_interval_seconds=10,
            healing_check_interval=15,
            degraded_threshold=60,
            queue_threshold=5,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAgent stopped.")
