# Re-running `terraform apply` after the function source changes redeploys
# automatically -- the source hash changes the uploaded object name below,
# which Terraform sees as a diff on the function's build_config.

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
      key        = "DISCORD_USER_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.discord_user_id.secret_id
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

# Lets infra-deploy.yml call this function's ?smoke_test=1 path right after
# every apply, using the same OIDC-federated deployer identity it already
# authenticates as -- no new secret/key needed for this.
resource "google_cloud_run_service_iam_member" "position_monitor_invoker_deployer" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.position_monitor.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:github-actions-deployer@${var.project_id}.iam.gserviceaccount.com"
}
