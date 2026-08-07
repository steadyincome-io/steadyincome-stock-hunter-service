resource "google_service_account" "candidate_finder" {
  account_id   = "candidate-finder-sa"
  project      = var.project_id
  display_name = "candidate_finder Cloud Function runtime identity"
}

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

# Impersonated by the SEPARATE pipeline.yml workflow (the main SEC-fetching
# pipeline), not by infra-deploy.yml -- deliberately its own identity, scoped
# to nothing but writing into the db-snapshot bucket, rather than reusing the
# much more powerful github-actions-deployer service account for a job that
# only needs to upload one file.
resource "google_service_account" "pipeline_uploader" {
  account_id   = "pipeline-uploader-sa"
  project      = var.project_id
  display_name = "Main SEC pipeline -> db-snapshot bucket uploader"
}

resource "google_storage_bucket_iam_member" "pipeline_uploader_writes_db_snapshot" {
  bucket = google_storage_bucket.db_snapshot.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline_uploader.email}"
}

# Lets pipeline.yml's GitHub Actions runs impersonate this service account via
# the same Workload Identity Pool/Provider used for infra-deploy.yml -- no
# separate pool needed, a provider can back multiple distinct bindings.
resource "google_service_account_iam_member" "pipeline_uploader_workload_identity" {
  service_account_id = google_service_account.pipeline_uploader.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${var.github_org}/${var.github_repo}"
}

# --- candidate_finder permissions ------------------------------------------

resource "google_storage_bucket_iam_member" "candidate_finder_reads_db_snapshot" {
  bucket = google_storage_bucket.db_snapshot.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.candidate_finder.email}"
}

resource "google_secret_manager_secret_iam_member" "candidate_finder_reads_webhook" {
  secret_id = google_secret_manager_secret.discord_webhook_url.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.candidate_finder.email}"
}

resource "google_secret_manager_secret_iam_member" "candidate_finder_reads_channel" {
  secret_id = google_secret_manager_secret.discord_channel_id.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.candidate_finder.email}"
}

resource "google_secret_manager_secret_iam_member" "candidate_finder_reads_sheet_id" {
  secret_id = google_secret_manager_secret.google_sheet_id.secret_id
  project   = var.project_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.candidate_finder.email}"
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
