"""Placeholder Cloud Function -- real screening logic (downloading the db
snapshot from GCS, running the premium_screener ranking, posting candidates
to Discord and the Google Sheet) is not implemented yet. This exists only so
Terraform has something valid to deploy while the infra is being stood up
and verified end-to-end (scheduler -> auth -> function -> 200 OK)."""
import functions_framework


@functions_framework.http
def main(request):
    return ("candidate_finder placeholder -- not yet implemented", 200)
