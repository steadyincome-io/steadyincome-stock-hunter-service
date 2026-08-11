# Every 20 minutes, market hours only (9am-4pm local), weekdays only -- no
# point burning invocations overnight/weekends when nothing can be closed
# anyway. This is 1 of your 3 free Cloud Scheduler jobs.
resource "google_cloud_scheduler_job" "position_monitor_every_20_min" {
  name      = "position-monitor-every-20-min"
  project   = var.project_id
  region    = var.region
  schedule  = "*/20 9-16 * * 1-5"
  time_zone = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.position_monitor.url

    oidc_token {
      service_account_email = google_service_account.scheduler_invoker.email
      audience              = google_cloudfunctions2_function.position_monitor.url
    }
  }

  depends_on = [google_cloud_run_service_iam_member.position_monitor_invoker]
}
