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
