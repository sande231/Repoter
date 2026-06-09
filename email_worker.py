"""Standalone worker process that processes the durable email queue."""
from email_queue import worker_loop


if __name__ == "__main__":
    worker_loop()
