# Both functions currently deploy placeholder source (infra/functions/*/main.py)
# -- real screening/monitoring logic isn't written yet. This lets the infra be
# stood up and verified end-to-end (scheduler -> auth -> function -> 200 OK)
# before that logic exists; re-running `terraform apply` after the real code
# is written redeploys automatically, since the source hash changes the
# uploaded object name below.

data "archive_file" "candidate_finder_source" {
  type        = "zip"
  source_dir  = "${path.module}/../functions/candidate_finder"
  output_path = "${path.module}/.build/candidate_finder.zip"
}

resource "google_storage_bucket_object" "candidate_finder_source" {
  name   = "candidate_finder-${data.archive_file.candidate_finder_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.candidate_finder_source.output_path
}

resource "google_cloudfunctions2_function" "candidate_finder" {
  name     = "candidate-finder"
  project  = var.project_id
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.candidate_finder_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    available_memory      = "512Mi"
    timeout_seconds       = 300
    service_account_email = google_service_account.candidate_finder.email

    environment_variables = {
      DB_SNAPSHOT_BUCKET = google_storage_bucket.db_snapshot.name
      DB_SNAPSHOT_OBJECT = "drawdown_analyzer.db"
    }

    secret_environment_variables {
      key        = "DISCORD_WEBHOOK_URL"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_webhook_url.secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "DISCORD_CHANNEL_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_channel_id.secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "GOOGLE_SHEET_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.google_sheet_id.secret_id
      version    = "latest"
    }
  }

  depends_on = [google_project_service.required]
}

# Only the scheduler-invoker identity may call this function -- no public
# "allUsers" invoker binding exists anywhere in this config.
resource "google_cloud_run_service_iam_member" "candidate_finder_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.candidate_finder.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_invoker.email}"
}

data "archive_file" "position_monitor_source" {
  type        = "zip"
  source_dir  = "${path.module}/../functions/position_monitor"
  output_path = "${path.module}/.build/position_monitor.zip"
}

resource "google_storage_bucket_object" "position_monitor_source" {
  name   = "position_monitor-${data.archive_file.position_monitor_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.position_monitor_source.output_path
}

resource "google_cloudfunctions2_function" "position_monitor" {
  name     = "position-monitor"
  project  = var.project_id
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.position_monitor_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 1
    available_memory      = "512Mi"
    timeout_seconds       = 300
    service_account_email = google_service_account.position_monitor.email

    secret_environment_variables {
      key        = "DISCORD_WEBHOOK_URL"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_webhook_url.secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "DISCORD_BOT_TOKEN"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_bot_token.secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "DISCORD_CHANNEL_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_channel_id.secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "GOOGLE_SHEET_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.google_sheet_id.secret_id
      version    = "latest"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_service_iam_member" "position_monitor_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.position_monitor.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_invoker.email}"
}
