# position_monitor was decommissioned -- trade tracking moved to fully
# manual (Google Sheet, reviewed weekly), so the automated Cloud Function
# that watched open positions and pinged Discord no longer has a job to do.
# All its resources (this file, scheduler.tf, storage.tf, service_accounts.tf,
# secrets.tf) were removed from Terraform config together and torn down via
# a normal `terraform apply` -- see infra/MANUAL_SETUP.md's "Decommissioning"
# section for the full teardown record (what was destroyed, when, and how to
# rebuild from scratch if this is ever revived).
