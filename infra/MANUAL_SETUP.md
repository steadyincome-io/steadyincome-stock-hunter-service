# Manual GCP/Discord/Sheets setup

Everything in this file is a **one-time, human-run step** — none of it can (or
should) be automated by the `infra-deploy.yml` Terraform pipeline. Once these
are done, pushing to `main` (with changes under `infra/**`) deploys/updates
everything else automatically via Workload Identity Federation, with no
long-lived keys stored anywhere.

Reference values used throughout:

| Value | Value used in this project |
|---|---|
| GCP project ID | `stock-hunter-trading` |
| GCP project number | `444713174784` |
| GitHub org | `steadyincome-io` |
| GitHub repo | `steadyincome-stock-hunter-service` |
| Workload Identity Provider | `projects/444713174784/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| Deployer service account | `github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com` |
| candidate_finder service account | `candidate-finder-sa@stock-hunter-trading.iam.gserviceaccount.com` |
| position_monitor service account | `position-monitor-sa@stock-hunter-trading.iam.gserviceaccount.com` |

---

## Part A — Local gcloud CLI setup

**1. Install the gcloud CLI**
```bash
brew install --cask google-cloud-sdk
```
(or `curl https://sdk.cloud.google.com | bash` if you don't use Homebrew)

**2. Authenticate as yourself** (opens a browser for normal Google OAuth login, no files to download)
```bash
gcloud auth login
```

**3. Create the GCP project** (skip if it already exists)
```bash
gcloud projects create stock-hunter-trading --name="stock-hunter-trading"
```

**4. Enable billing on the project** (the one step that isn't pure CLI — needs a billing account/payment method first, created in the console)
```bash
# First, at https://console.cloud.google.com/billing -- create a billing account if you don't have one
gcloud billing accounts list   # note the ACCOUNT_ID
gcloud billing projects link stock-hunter-trading --billing-account=ACCOUNT_ID
```

**5. Set this as your active project**
```bash
gcloud config set project stock-hunter-trading
```

---

## Part B — Workload Identity Federation (GCP trusts GitHub's OIDC tokens)

This is what lets `infra-deploy.yml` authenticate to GCP with no stored key.

**6. Enable required APIs**
```bash
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  --project="stock-hunter-trading"
```

**7. Create a Workload Identity Pool**
```bash
gcloud iam workload-identity-pools create "github-pool" \
  --project="stock-hunter-trading" \
  --location="global" \
  --display-name="GitHub Actions Pool"
```

**8. Create a Workload Identity Provider pointed at GitHub's OIDC issuer**
```bash
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project="stock-hunter-trading" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner == 'steadyincome-io'" \
  --issuer-uri="https://token.actions.githubusercontent.com"
```

**9. Create the service account GitHub Actions will impersonate**
```bash
gcloud iam service-accounts create "github-actions-deployer" \
  --project="stock-hunter-trading" \
  --display-name="GitHub Actions Deployer"
```

**10. Grant that service account the roles it needs to deploy the infra**
```bash
for ROLE in roles/cloudfunctions.admin roles/cloudscheduler.admin roles/storage.admin \
            roles/secretmanager.admin roles/iam.serviceAccountUser \
            roles/iam.serviceAccountAdmin roles/run.admin \
            roles/serviceusage.serviceUsageAdmin; do
  gcloud projects add-iam-policy-binding stock-hunter-trading \
    --member="serviceAccount:github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com" \
    --role="$ROLE"
done
```
> **Second correction:** `roles/serviceusage.serviceUsageAdmin` was also missing.
> `main.tf`'s `google_project_service.required` resources enable APIs
> (Cloud Functions, Scheduler, Secret Manager, Sheets, Drive, Cloud Build,
> Artifact Registry, Run) on the project -- a completely separate permission
> domain ("Service Usage") from anything the other roles above grant. Without
> it, `terraform apply` fails immediately at that step with
> `AUTH_PERMISSION_DENIED: Permission denied to enable service [...]` for every
> API in the list, before creating anything that depends on them. If you
> already applied without it:
> ```bash
> gcloud projects add-iam-policy-binding stock-hunter-trading \
>   --member="serviceAccount:github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com" \
>   --role="roles/serviceusage.serviceUsageAdmin"
> ```
> **Correction (added after the fact):** `roles/iam.serviceAccountAdmin` was missing
> from the original list. `roles/iam.serviceAccountUser` alone only lets you *attach*
> an existing service account to a resource -- it doesn't let Terraform *create* new
> service accounts or grant IAM policies on them (e.g. the `workloadIdentityUser`
> binding the pipeline-uploader service account needs), both of which
> `service_accounts.tf` does.
>
> We tried a narrower alternative first (`roles/iam.serviceAccountCreator` +
> `roles/iam.serviceAccountIamAdmin` instead of the broader `serviceAccountAdmin`)
> to avoid granting delete/disable rights on service accounts. `serviceAccountCreator`
> bound fine at the project level, but `serviceAccountIamAdmin` was hard-rejected by
> the API with `INVALID_ARGUMENT: ... is not supported for this resource` when bound
> at the project level -- a real GCP platform restriction on that specific role, not
> a permissions issue we could work around (and `pipeline-uploader-sa` doesn't exist
> yet at this point in setup, so binding it at the specific-service-account level
> instead isn't an option either). `roles/iam.serviceAccountAdmin` bound successfully
> at the project level, so that's what's actually used. If you already ran the
> narrower version, `serviceAccountCreator` is now a redundant subset of
> `serviceAccountAdmin` -- harmless to leave, or remove if you want:
> ```bash
> gcloud projects add-iam-policy-binding stock-hunter-trading \
>   --member="serviceAccount:github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com" \
>   --role="roles/iam.serviceAccountAdmin"
> ```

**11. Restrict impersonation to only this specific repo**
```bash
PROJECT_NUMBER=$(gcloud projects describe stock-hunter-trading --format="value(projectNumber)")

gcloud iam service-accounts add-iam-policy-binding \
  "github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com" \
  --project="stock-hunter-trading" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/steadyincome-io/steadyincome-stock-hunter-service"
```

**12. Get the provider's full resource name** (already confirmed for this project — recorded in the reference table above, and hardcoded into `.github/workflows/infra-deploy.yml`)
```bash
gcloud iam workload-identity-pools providers describe "github-provider" \
  --project="stock-hunter-trading" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --format="value(name)"
```

**Status: done.** Confirmed output was
`projects/444713174784/locations/global/workloadIdentityPools/github-pool/providers/github-provider`.

---

## Part C — Bootstrap the Terraform state bucket

Terraform can't create the bucket that holds its own state, so this one has
to exist *before* the first `terraform init`. This is a one-time step —
every subsequent `terraform init` (including the ones inside
`infra-deploy.yml` on every push) just connects to this already-existing
bucket automatically, no manual step needed again.

```bash
gcloud storage buckets create gs://stock-hunter-trading-tfstate \
  --project=stock-hunter-trading --location=us-central1 \
  --uniform-bucket-level-access

gcloud storage buckets update gs://stock-hunter-trading-tfstate --versioning

cat > /tmp/tfstate-lifecycle.json <<'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"numNewerVersions": 2}
    }
  ]
}
EOF

gcloud storage buckets update gs://stock-hunter-trading-tfstate \
  --lifecycle-file=/tmp/tfstate-lifecycle.json
```
(`numNewerVersions: 2` keeps the current live state plus 1 previous version, pruning anything older.)

---

## Gotchas hit on the first real `terraform apply`

Two errors showed up that weren't visible from `terraform validate`/`plan` alone
-- both are one-time, project-level fixes, already applied for this project, but
recorded here in case this is ever rebuilt from scratch.

**1. `candidate_finder` failed with an Artifact Registry permission error:**
```
Unable to retrieve the repository metadata for .../repositories/gcf-artifacts.
Ensure that the Cloud Functions service agent has 'artifactregistry.repositories.list'
and 'artifactregistry.repositories.get' permissions.
```
This is a known first-use-of-Cloud-Functions-gen2-in-a-project quirk: Google
auto-creates a "Cloud Functions service agent"
(`service-PROJECT_NUMBER@gcf-admin-robot.iam.gserviceaccount.com`), but it doesn't
always get `roles/artifactregistry.reader` on the auto-created build-artifacts
repo automatically. Fix (this identity can't even be `describe`d by a normal
Owner account -- that's expected for Google-managed service agents -- just bind
the role directly):
```bash
PROJECT_NUMBER=$(gcloud projects describe stock-hunter-trading --format="value(projectNumber)")
gcloud projects add-iam-policy-binding stock-hunter-trading \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcf-admin-robot.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.reader"
```

**2. `position_monitor` failed with `Secret ... /versions/latest was not found`**
for all 4 secrets. **Correction to Parts D/E below:** secret values are needed
*before* the functions can deploy, not "after the first successful apply" as
originally written here -- `secret_environment_variables` referencing
`version = "latest"` has nothing to resolve if the secret has zero versions.
Fix: add at least a placeholder value to each secret so a version exists, then
come back and do Parts D/E properly (create the real webhook/bot/sheet) and
overwrite the placeholders with real values -- adding a new secret version
later is exactly how you rotate/replace one, no Terraform change needed:
```bash
for SECRET in discord-webhook-url discord-bot-token discord-channel-id google-sheet-id; do
  echo -n "PLACEHOLDER_REPLACE_ME" | gcloud secrets versions add "$SECRET" --data-file=- --project=stock-hunter-trading
done
```

---

## Part D — Discord webhook + bot token

Ideally done **before** the first `terraform apply` (see the gotcha above --
the functions won't deploy with an empty secret). If you already applied with
placeholder values, these steps just add a new version of each secret with the
real value; no Terraform change or re-apply needed for that.

**1. Create the webhook** (for sending "candidate found"/"close now" notifications — no bot needed for this direction):
Discord → target channel → Edit Channel → Integrations → Webhooks → New Webhook → copy the URL.

**2. Create the bot** (for reading back your emoji-reaction confirmations — a webhook can't do this, only a bot can):
- Go to https://discord.com/developers/applications → New Application
- Bot tab → Add Bot → copy the token
- OAuth2 → URL Generator → scope `bot` → permissions `View Channel` + `Read Message History` (add `Send Messages` too if you want the bot itself able to post, not just the webhook) → open the generated URL and invite it to your server

**3. Get the channel ID**: enable Developer Mode (Discord Settings → Advanced → Developer Mode), then right-click the target channel → Copy Channel ID.

**4. Enter each value into Secret Manager via the browser console** (avoids the token sitting in your shell history):
- Console → Secret Manager → click the secret name → "New Version" → paste the value → Save
- Do this for `discord-webhook-url`, `discord-bot-token`, and `discord-channel-id`

(If you'd rather use the CLI, avoid shell-history exposure with `read -s` instead of a literal `echo "token" | ...`:
```bash
read -s -p "Paste bot token: " TOKEN
echo -n "$TOKEN" | gcloud secrets versions add discord-bot-token --data-file=- --project=stock-hunter-trading
unset TOKEN
```
)

---

## Part E — Google Sheet (trade tracking)

The sheet must be created and owned by **you**, not a service account — a
service-account-created Drive file wouldn't show up in your own Drive and
has its own ownership/quota quirks. The standard pattern is: you own it,
the service accounts are just granted Editor access.

**1. Create the sheet** at https://sheets.google.com (a blank sheet is fine). Set up your own header row (ticker, strategy, strike(s), entry credit, entry date, profit target, stop level, status, Discord message ID, etc.) — deciding the exact columns is on you, not the code, since the whole point of Sheets here is that you can see and understand what's being tracked.

**2. Grab the sheet ID** from its URL:
```
https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_SHEET_ID/edit
```

**3. Share the sheet** (Share button, top right) with both function service accounts, set to **Editor**:
- `candidate-finder-sa@stock-hunter-trading.iam.gserviceaccount.com`
- `position-monitor-sa@stock-hunter-trading.iam.gserviceaccount.com`

**4. Add the sheet ID to Secret Manager** (same as Part D, step 4 — via console or the `read -s` CLI pattern):
- Secret name: `google-sheet-id`

---

## Quick status checklist

- [x] Part A — local gcloud CLI installed, authenticated, project created, billing linked
- [x] Part B — Workload Identity Federation configured and verified
- [x] Part C — Terraform state bucket bootstrapped
- [ ] Part D — Discord webhook + bot created, secrets populated (currently placeholder values only)
- [ ] Part E — Google Sheet created, shared, sheet ID secret populated (currently placeholder value only)
