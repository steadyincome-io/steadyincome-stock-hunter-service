provider "google" {
  project = var.project_id
  region  = var.region
}

# The APIs this used to enable (cloudfunctions, run, cloudbuild,
# cloudscheduler, secretmanager, storage, sheets, drive, artifactregistry)
# are left enabled on the project -- disable_on_destroy was false, so
# removing this resource from config doesn't disable them, it just stops
# Terraform from tracking the enablement. See infra/MANUAL_SETUP.md
# "Decommissioning" -- position_monitor (the only thing that used these) was
# torn down; nothing currently in this repo runs on GCP.
