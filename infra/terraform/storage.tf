# Holds the drawdown_analyzer.db snapshot uploaded by the main pipeline
# (.github/workflows/pipeline.yml) after each run -- this is how the
# otherwise-stateless Cloud Functions get access to the screening analytics
# (quality/risk/distress/concentration scores) computed there. Only the
# current snapshot ever matters (candidate_finder always wants the latest),
# so no versioning here -- each upload just overwrites the previous object.
resource "google_storage_bucket" "db_snapshot" {
  name          = "${var.project_id}-db-snapshot"
  project       = var.project_id
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  depends_on = [google_project_service.required]
}

# Holds zipped Cloud Function source code, uploaded by this Terraform config
# itself on every apply (see functions.tf). Not app data -- safe to
# force_destroy since it's fully reproducible from the repo.
resource "google_storage_bucket" "function_source" {
  name          = "${var.project_id}-function-source"
  project       = var.project_id
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true

  depends_on = [google_project_service.required]
}
