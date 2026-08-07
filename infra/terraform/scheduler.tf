# Fires once on Wednesdays -- adjust the schedule/time_zone if you want a
# different day/time. This is 1 of your 3 free Cloud Scheduler jobs.
resource "google_cloud_scheduler_job" "candidate_finder_weekly" {
  name      = "candidate-finder-weekly"
  project   = var.project_id
  region    = var.region
  schedule  = "0 11 * * 3"
  time_zone = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.candidate_finder.url

    oidc_token {
      service_account_email = google_service_account.scheduler_invoker.email
      audience              = google_cloudfunctions2_function.candidate_finder.url
    }
  }

  depends_on = [google_cloud_run_service_iam_member.candidate_finder_invoker]
}

# Every 10 minutes, market hours only (9am-4pm local), weekdays only -- no
# point burning invocations overnight/weekends when nothing can be filled or
# closed anyway. This is 2 of your 3 free Cloud Scheduler jobs.
resource "google_cloud_scheduler_job" "position_monitor_every_10_min" {
  name      = "position-monitor-every-10-min"
  project   = var.project_id
  region    = var.region
  schedule  = "*/10 9-16 * * 1-5"
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
