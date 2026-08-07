output "db_snapshot_bucket" {
  value       = google_storage_bucket.db_snapshot.name
  description = "Upload drawdown_analyzer.db here from pipeline.yml after each run"
}

output "candidate_finder_url" {
  value = google_cloudfunctions2_function.candidate_finder.url
}

output "position_monitor_url" {
  value = google_cloudfunctions2_function.position_monitor.url
}

output "candidate_finder_service_account" {
  value = google_service_account.candidate_finder.email
}

output "position_monitor_service_account" {
  value = google_service_account.position_monitor.email
}

output "pipeline_uploader_service_account" {
  value       = google_service_account.pipeline_uploader.email
  description = "Used by pipeline.yml (a separate workflow) to upload drawdown_analyzer.db"
}

output "next_manual_steps" {
  description = "Run these once after the first successful apply"
  value       = <<-EOT
    1. Share your Google Sheet with these two service accounts as Editor:
       - ${google_service_account.candidate_finder.email}
       - ${google_service_account.position_monitor.email}

    2. Add the real secret values (never via Terraform -- see secrets.tf):
       echo -n "<your webhook url>"  | gcloud secrets versions add discord-webhook-url --data-file=- --project=${var.project_id}
       echo -n "<your bot token>"    | gcloud secrets versions add discord-bot-token   --data-file=- --project=${var.project_id}
       echo -n "<your channel id>"   | gcloud secrets versions add discord-channel-id  --data-file=- --project=${var.project_id}
       echo -n "<your sheet id>"     | gcloud secrets versions add google-sheet-id     --data-file=- --project=${var.project_id}
  EOT
}
