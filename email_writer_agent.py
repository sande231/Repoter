import os
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from agent_sdk import AgentSDK


class EmailWriterAgent:
    def __init__(self):
        self.agent_id = "email-writer-agent"
        self.sdk = AgentSDK(
            agent_id=self.agent_id,
            ingestion_url="http://localhost:5000",
            tags={"agent_type": "custom", "service": "email-writer"},
        )

        # Email composition config read from environment
        self.smtp_host = os.environ.get("EMAIL_SMTP_HOST")
        self.smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("EMAIL_SMTP_USER")
        self.smtp_password = os.environ.get("EMAIL_SMTP_PASSWORD")
        self.sender_name = os.environ.get("EMAIL_SENDER_NAME", "Synapse Agent")

        # Email content config read from environment
        self.recipient_address = os.environ.get("EMAIL_RECIPIENT_ADDRESS")
        self.email_subject = os.environ.get("EMAIL_SUBJECT", "Hello from Synapse")
        self.email_topic = os.environ.get(
            "EMAIL_TOPIC",
            "a friendly greeting and a brief project status update",
        )

    def register(self):
        self.sdk.register(
            {
                "name": "Email Writer Agent",
                "type": "communication",
                "description": (
                    "Composes and sends a plain-text email on a configurable topic. "
                    "SMTP credentials, recipient, subject, and topic are all supplied "
                    "via environment variables. The agent reports send success/failure "
                    "as real telemetry."
                ),
                "version": "1.0.0",
            }
        )

    def _check_required_env_vars(self) -> bool:
        """Return True if all required env vars are present, else report problems and return False."""
        required = {
            "EMAIL_SMTP_HOST": self.smtp_host,
            "EMAIL_SMTP_USER": self.smtp_user,
            "EMAIL_SMTP_PASSWORD": self.smtp_password,
            "EMAIL_RECIPIENT_ADDRESS": self.recipient_address,
        }
        missing = [name for name, val in required.items() if not val]
        if missing:
            self.sdk.report_problem(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set them before running this agent.",
                severity="critical",
                details={"missing_vars": missing},
            )
            return False
        return True

    def _compose_email_body(self) -> str:
        """
        Build the email body text.

        NOTE: This implementation uses a simple template. If you want AI-generated
        body text you would plug in an LLM API call here (e.g. OpenAI, Anthropic).
        Replace the return value below with your preferred generation call.
        """
        topic = self.email_topic
        body = (
            f"Hello,\n\n"
            f"This email was composed automatically by the Synapse Email Writer Agent.\n\n"
            f"Topic: {topic}\n\n"
            f"Please treat this as a placeholder body. To generate richer content, "
            f"integrate an LLM or template engine in the _compose_email_body() method "
            f"of email_writer_agent.py.\n\n"
            f"Best regards,\n"
            f"{self.sender_name}"
        )
        return body

    def compose_and_send(self):
        """Compose an email and attempt to send it via SMTP."""
        if not self._check_required_env_vars():
            return

        body = self._compose_email_body()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = self.email_subject
        msg["From"] = f"{self.sender_name} <{self.smtp_user}>"
        msg["To"] = self.recipient_address
        msg.attach(MIMEText(body, "plain"))

        start_time = time.time()
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, self.recipient_address, msg.as_string())

            elapsed_ms = int((time.time() - start_time) * 1000)
            self.sdk.send_metrics(
                {
                    "emails_sent": 1,
                    "send_latency_ms": elapsed_ms,
                    "recipient": self.recipient_address,
                    "subject": self.email_subject,
                    "smtp_host": self.smtp_host,
                    "smtp_port": self.smtp_port,
                    "status": "sent",
                }
            )
            print(
                f"[email-writer-agent] Email sent to {self.recipient_address} "
                f"in {elapsed_ms} ms."
            )

        except smtplib.SMTPAuthenticationError as exc:
            self.sdk.report_problem(
                f"SMTP authentication failed for user {self.smtp_user}: {exc}",
                severity="critical",
                details={
                    "smtp_host": self.smtp_host,
                    "smtp_user": self.smtp_user,
                    "error": str(exc),
                },
            )
        except smtplib.SMTPException as exc:
            self.sdk.report_problem(
                f"SMTP error while sending email: {exc}",
                severity="critical",
                details={
                    "smtp_host": self.smtp_host,
                    "recipient": self.recipient_address,
                    "error": str(exc),
                },
            )
        except OSError as exc:
            self.sdk.report_problem(
                f"Network error reaching SMTP host {self.smtp_host}:{self.smtp_port}: {exc}",
                severity="critical",
                details={
                    "smtp_host": self.smtp_host,
                    "smtp_port": self.smtp_port,
                    "error": str(exc),
                },
            )


if __name__ == "__main__":
    # How often to send (seconds). Default: 3600 (once per hour).
    interval = int(os.environ.get("EMAIL_SEND_INTERVAL_SECONDS", "3600"))

    agent = EmailWriterAgent()
    agent.register()

    while True:
        agent.compose_and_send()
        print(f"[email-writer-agent] Sleeping {interval}s until next send.")
        time.sleep(interval)