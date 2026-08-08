resource "google_service_account" "position_monitor" {
  account_id   = "position-monitor-sa"
  project      = var.project_id
  display_name = "position_monitor Cloud Function runtime identity"
}

resource "google_service_account" "scheduler_invoker" {
  account_id   = "scheduler-invoker-sa"
  project      = var.project_id
  display_name = "Cloud Scheduler -> Cloud Functions invoker identity"
}

# Sheets/Drive API access is granted by sharing the Sheet itself with this
# service account's email (like sharing with any Google account) -- not a
# GCP IAM role. That's a manual, one-time step (see outputs.tf).

# --- position_monitor permissions ------------------------------------------

resource "google_secret_manager_secret_iam_member" "position_monitor_reads_webhook" {
  secret_id = google_secret_manager_secret.discord_webhook_url.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.position_monitor.email}"
}

resource "google_secret_manager_secret_iam_member" "position_monitor_reads_bot_token" {
  secret_id = google_secret_manager_secret.discord_bot_token.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.position_monitor.email}"
}

resource "google_secret_manager_secret_iam_member" "position_monitor_reads_channel" {
  secret_id = google_secret_manager_secret.discord_channel_id.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.position_monitor.email}"
}

resource "google_secret_manager_secret_iam_member" "position_monitor_reads_sheet_id" {
  secret_id = google_secret_manager_secret.google_sheet_id.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.position_monitor.email}"
}

resource "google_secret_manager_secret_iam_member" "position_monitor_reads_user_id" {
  secret_id = google_secret_manager_secret.discord_user_id.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.position_monitor.email}"
}
