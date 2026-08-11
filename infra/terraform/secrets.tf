# Secret CONTAINERS only -- Terraform never sets the actual secret VALUES, so
# webhook URLs/tokens never end up in Terraform state or git history. After
# the first `terraform apply`, add the real values with (see also the
# `next_manual_steps` output at the bottom of outputs.tf):
#   echo -n "https://discord.com/api/webhooks/..." | gcloud secrets versions add discord-webhook-url --data-file=- --project=stock-hunter-trading
#   echo -n "your-bot-token"                        | gcloud secrets versions add discord-bot-token   --data-file=- --project=stock-hunter-trading
#   echo -n "your-channel-id"                        | gcloud secrets versions add discord-channel-id  --data-file=- --project=stock-hunter-trading
#   echo -n "your-discord-user-id"                   | gcloud secrets versions add discord-user-id     --data-file=- --project=stock-hunter-trading
#   echo -n "your-google-sheet-id"                   | gcloud secrets versions add google-sheet-id      --data-file=- --project=stock-hunter-trading
#
# A prior version of this file also had Terraform auto-create a
# "PLACEHOLDER_REPLACE_ME" google_secret_manager_secret_version for every
# secret above, so the first-ever deploy wouldn't fail on
# secret_environment_variables' `version = "latest"` finding nothing. That was
# REMOVED after it caused a real production incident: once each secret's
# Terraform-tracked placeholder version got destroyed out-of-band (e.g.
# cleaning up old versions from the Secret Manager console after adding a
# real value), the next `terraform apply` saw that tracked object gone and
# silently recreated it -- and because Secret Manager's `version = "latest"`
# always means "most recently created enabled version" regardless of who
# created it, that fresh placeholder immediately became the new latest and
# shadowed the real value, breaking position_monitor with zero warning. This
# could recur on ANY future apply, not just the first one -- too dangerous to
# keep automated. If you ever add a brand-new secret here, add its first
# version manually with a one-off `gcloud secrets versions add` (placeholder
# or real, doesn't matter) right after `terraform apply` creates the
# container, the same way as the real values above -- never let Terraform
# manage secret version content for anything Discord/Sheet-related.

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

# Whose reaction counts as a real confirmation -- position_monitor checks
# incoming reactions against this Discord user ID before treating a ✅ as
# "I actually placed/closed this trade" (find your ID: enable Developer Mode
# in Discord, right-click your own name, Copy User ID).
resource "google_secret_manager_secret" "discord_user_id" {
  secret_id = "discord-user-id"
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

