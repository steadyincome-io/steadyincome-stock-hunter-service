variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "stock-hunter-trading"
}

variable "region" {
  description = "GCP region for Cloud Functions, Scheduler, and Storage"
  type        = string
  default     = "us-central1"
}

variable "scheduler_time_zone" {
  description = "IANA time zone the Cloud Scheduler cron expressions are evaluated in"
  type        = string
  default     = "America/New_York"
}
