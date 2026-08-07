provider "google" {
  project = var.project_id
  region  = var.region
}

# APIs beyond what the Workload Identity Federation bootstrap already enabled
# (iam/iamcredentials/sts/cloudresourcemanager) -- these are what the actual
# trading-assistant infra runs on.
locals {
  required_apis = [
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "sheets.googleapis.com",
    "drive.googleapis.com",
    "artifactregistry.googleapis.com",
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_apis)
  project  = var.project_id
  service  = each.value

  disable_on_destroy = false
}
