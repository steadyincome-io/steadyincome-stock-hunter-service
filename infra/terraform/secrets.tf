# Secret CONTAINERS only -- Terraform never sets the actual secret values, so
# webhook URLs/tokens never end up in Terraform state or git history. After
# the first `terraform apply`, add the real values with (see also the
# `next_manual_steps` output at the bottom of outputs.tf):
#   echo -n "https://discord.com/api/webhooks/..." | gcloud secrets versions add discord-webhook-url --data-file=- --project=stock-hunter-trading
#   echo -n "your-bot-token"                        | gcloud secrets versions add discord-bot-token   --data-file=- --project=stock-hunter-trading
#   echo -n "your-channel-id"                        | gcloud secrets versions add discord-channel-id  --data-file=- --project=stock-hunter-trading
#   echo -n "your-google-sheet-id"                   | gcloud secrets versions add google-sheet-id      --data-file=- --project=stock-hunter-trading

resource "google_secret_manager_secret" "discord_webhook_url" {
  secret_id = "discord-webhook-url"
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "discord_bot_token" {
  secret_id = "discord-bot-token"
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "discord_channel_id" {
  secret_id = "discord-channel-id"
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "google_sheet_id" {
  secret_id = "google-sheet-id"
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}
