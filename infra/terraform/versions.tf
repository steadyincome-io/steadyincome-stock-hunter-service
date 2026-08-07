terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # This bucket must exist BEFORE the first `terraform init` -- Terraform
  # can't create the bucket that holds its own state. Bootstrap it once with:
  #   gcloud storage buckets create gs://stock-hunter-trading-tfstate \
  #     --project=stock-hunter-trading --location=us-central1 \
  #     --uniform-bucket-level-access
  #   gcloud storage buckets update gs://stock-hunter-trading-tfstate --versioning
  backend "gcs" {
    bucket = "stock-hunter-trading-tfstate"
    prefix = "premium-screener-infra"
  }
}
