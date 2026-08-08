output "position_monitor_url" {
  value = google_cloudfunctions2_function.position_monitor.url
}

output "position_monitor_service_account" {
  value = google_service_account.position_monitor.email
}

output "next_manual_steps" {
  description = "Run these once after the first successful apply"
  value       = <<-EOT
    1. Share your Google Sheet with this service account as Editor:
       - ${google_service_account.position_monitor.email}

    2. Add the real secret values (never via Terraform -- see secrets.tf):
       echo -n "<your webhook url>"  | gcloud secrets versions add discord-webhook-url --data-file=- --project=${var.project_id}
       echo -n "<your bot token>"    | gcloud secrets versions add discord-bot-token   --data-file=- --project=${var.project_id}
       echo -n "<your channel id>"   | gcloud secrets versions add discord-channel-id  --data-file=- --project=${var.project_id}
       echo -n "<your discord user id>" | gcloud secrets versions add discord-user-id  --data-file=- --project=${var.project_id}
       echo -n "<your sheet id>"     | gcloud secrets versions add google-sheet-id     --data-file=- --project=${var.project_id}
  EOT
}
