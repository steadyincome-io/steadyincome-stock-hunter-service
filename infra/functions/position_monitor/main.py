"""Placeholder Cloud Function -- real position-monitoring logic (reading open
trades from the Google Sheet, checking profit-target/stop-loss, polling
Discord for reaction confirmations) is not implemented yet. This exists only
so Terraform has something valid to deploy while the infra is being stood up
and verified end-to-end (scheduler -> auth -> function -> 200 OK)."""
import functions_framework


@functions_framework.http
def main(request):
    return ("position_monitor placeholder -- not yet implemented", 200)
