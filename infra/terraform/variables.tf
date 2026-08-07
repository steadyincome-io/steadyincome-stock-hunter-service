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

# These three describe the Workload Identity Federation trust set up manually
# in infra/MANUAL_SETUP.md Part B -- used here only to scope the pipeline
# uploader's workloadIdentityUser binding to this specific repo.
variable "project_number" {
  description = "GCP project number"
  type        = string
  default     = "444713174784"
}

variable "github_org" {
  description = "GitHub org/owner that owns the deploying repo"
  type        = string
  default     = "steadyincome-io"
}

variable "github_repo" {
  description = "GitHub repo name"
  type        = string
  default     = "steadyincome-stock-hunter-service"
}
