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
# The google_secret_manager_secret_version.placeholder resource below IS safe
# to manage via Terraform, unlike the above -- "PLACEHOLDER_REPLACE_ME" isn't
# sensitive, so it ending up in Terraform state costs nothing. Its only job is
# to guarantee every secret has at least one version the instant it's created,
# so a Cloud Function referencing it via secret_environment_variables
# (version = "latest") never fails to deploy with "version ... was not found"
# (the exact error this project hit twice already). Once you add a real value
# with the commands above, this resource's own tracked version is left alone
# -- Terraform doesn't re-read secret payload content on later applies (that
# would mean silently calling the Secret Manager access API, with its own
# audit-log/quota cost, on every plan), so it never reverts what you've set.

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

locals {
  # google_secret_manager_secret_version.secret needs the FULL resource path
  # (projects/{project}/secrets/{secret_id}), not the bare secret_id -- using
  # .secret_id here instead of .id produced a malformed API call
  # (.../v1/discord-webhook-url:addVersion, missing the projects/.../secrets/
  # prefix entirely) that 404'd on every secret.
  bootstrap_secret_ids = [
    google_secret_manager_secret.discord_webhook_url.id,
    google_secret_manager_secret.discord_bot_token.id,
    google_secret_manager_secret.discord_channel_id.id,
    google_secret_manager_secret.discord_user_id.id,
    google_secret_manager_secret.google_sheet_id.id,
  ]
}

resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = toset(local.bootstrap_secret_ids)
  secret      = each.value
  secret_data = "PLACEHOLDER_REPLACE_ME"
}
