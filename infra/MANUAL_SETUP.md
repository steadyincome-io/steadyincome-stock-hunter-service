# Manual GCP/Discord/Sheets setup

Everything in this file is a **one-time, human-run step** — none of it can (or
should) be automated by the `infra-deploy.yml` Terraform pipeline. Once these
are done, pushing to `main` (with changes under `infra/**`) deploys/updates
everything else automatically via Workload Identity Federation, with no
long-lived keys stored anywhere.

Architecture note: candidate-finding is **not** automated. You run
`premium_screener.py` yourself from your laptop whenever you want (weekly or
otherwise), and manually add a row to the trade-tracking Sheet once you've
actually placed a trade. The only thing running in GCP is `position_monitor`,
which watches your already-open positions and pings Discord when a
profit/stop-loss/expiration threshold is hit.

Reference values used throughout:

| Value | Value used in this project |
|---|---|
| GCP project ID | `stock-hunter-trading` |
| GCP project number | `444713174784` |
| GitHub org | `steadyincome-io` |
| GitHub repo | `steadyincome-stock-hunter-service` |
| Workload Identity Provider | `projects/444713174784/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| Deployer service account | `github-actions-deployer@stock-hunter-trading.iam.gserviceaccount.com` |
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
> **Correction:** `roles/serviceusage.serviceUsageAdmin` was originally missing.
> `main.tf`'s `google_project_service.required` resources enable APIs on the
> project -- a completely separate permission domain ("Service Usage") from
> anything the other roles grant. Without it, `terraform apply` fails
> immediately with `AUTH_PERMISSION_DENIED: Permission denied to enable
> service [...]` for every API in the list.
>
> **Correction:** `roles/iam.serviceAccountAdmin` was also originally missing.
> `roles/iam.serviceAccountUser` alone only lets you *attach* an existing
> service account to a resource -- it doesn't let Terraform *create* new
> service accounts (`service_accounts.tf` creates `position-monitor-sa` and
> `scheduler-invoker-sa`) or grant IAM policies on them. A narrower
> alternative (`serviceAccountCreator` + `serviceAccountIamAdmin`) was tried
> first to avoid granting delete/disable rights on service accounts;
> `serviceAccountIamAdmin` was hard-rejected by the API with
> `INVALID_ARGUMENT: ... is not supported for this resource` when bound at
> the project level -- a real GCP platform restriction on that specific role.
> `roles/iam.serviceAccountAdmin` bound successfully, so that's what's
> actually used.

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

**1. The first Cloud Functions gen2 deploy in this project failed with an Artifact Registry permission error:**
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
This is a project-level fix, done once -- it isn't specific to whichever
function happened to trigger it first.

**2. The function failed with `Secret ... /versions/latest was not found`**
for every secret it referenced. Secret values are needed *before* the
function can deploy, not "after the first successful apply" -- a
`secret_environment_variables` block referencing `version = "latest"` has
nothing to resolve if the secret has zero versions. Fix: add at least a
placeholder value to each secret so a version exists, then come back and do
Parts D/E properly and overwrite the placeholders with real values --
adding a new secret version later is exactly how you rotate/replace one, no
Terraform change needed:
```bash
for SECRET in discord-webhook-url discord-bot-token discord-channel-id discord-user-id google-sheet-id; do
  echo -n "PLACEHOLDER_REPLACE_ME" | gcloud secrets versions add "$SECRET" --data-file=- --project=stock-hunter-trading
done
```

---

## Part D — Discord webhook + bot token

Ideally done **before** the first `terraform apply` (see the gotcha above --
the function won't deploy with an empty secret). If you already applied with
placeholder values, these steps just add a new version of each secret with the
real value; no Terraform change or re-apply needed for that.

`position_monitor` posts a message like *"AAPL (put_credit_spread) -- profit
target reached."* with two reaction options: react **✅** once you've
actually closed the position, or **🚫** to take no action for now (it'll
re-check and may ask again on a later run if the same condition still holds
-- there's no snooze/cooldown).

**1. Create the webhook** (for sending those alerts — no bot needed for this direction):
Discord → target channel → Edit Channel → Integrations → Webhooks → New Webhook → copy the URL.

**2. Create the bot** (for reading back your emoji-reaction confirmations — a webhook can't do this, only a bot can):
- Go to https://discord.com/developers/applications → New Application
- Bot tab → Add Bot → copy the token
- OAuth2 → URL Generator → scope `bot` → permissions `View Channel` + `Read Message History` → open the generated URL and invite it to your server

**3. Get the channel ID**: enable Developer Mode (Discord Settings → Advanced → Developer Mode), then right-click the target channel → Copy Channel ID.

**4. Get your own Discord user ID** (this is whose reaction actually counts as a real confirmation): right-click your own username → Copy User ID.

**5. Enter each value into Secret Manager via the browser console** (avoids the token sitting in your shell history):
- Console → Secret Manager → click the secret name → "New Version" → paste the value → Save
- Do this for `discord-webhook-url`, `discord-bot-token`, `discord-channel-id`, and `discord-user-id`

(If you'd rather use the CLI, avoid shell-history exposure with `read -s` instead of a literal `echo "token" | ...`:
```bash
read -s -p "Paste bot token: " TOKEN
echo -n "$TOKEN" | gcloud secrets versions add discord-bot-token --data-file=- --project=stock-hunter-trading
unset TOKEN
```
)

---

## Part E — Google Sheet (trade tracking, manual entry)

The sheet must be created and owned by **you**, not a service account — a
service-account-created Drive file wouldn't show up in your own Drive and
has its own ownership/quota quirks. The standard pattern is: you own it, the
service account is just granted Editor access.

**1. Create the sheet** at https://sheets.google.com (a blank sheet is fine).

**Row 1 must be exactly this header row, in this order** (columns A-M) —
`position_monitor` reads/writes these exact names via the Sheets API:

```
ticker | strategy | short_strike | long_strike | expiration | credit | entry_date | status | discord_message_id | profit_target_pct | stop_loss_multiple | closed_date | close_reason
```

You can still add extra columns of your own further to the right (notes,
whatever) -- the function only ever touches A-M.

**2. Grab the sheet ID** from its URL:
```
https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_SHEET_ID/edit
```

**3. Share the sheet** (Share button, top right) with the function's service account, set to **Editor**:
- `position-monitor-sa@stock-hunter-trading.iam.gserviceaccount.com`

**4. Add the sheet ID to Secret Manager** (same as Part D, step 5 — via console or the `read -s` CLI pattern):
- Secret name: `google-sheet-id`

**5. Adding a trade -- entirely manual, no function does this for you:**
run `premium_screener.py` yourself, decide on a candidate, place the trade at
your broker, then add one row to the Sheet:

| column | what to put |
|---|---|
| `ticker`, `short_strike`, `long_strike`, `expiration`, `credit` | copy straight from the screener's output for the trade you took |
| `strategy` | **exactly** one of the 4 values below -- this is how the function knows which side of the option chain to check and whether to expect one leg or two |
| `entry_date` | today's date |
| `status` | `open` |
| `discord_message_id`, `closed_date`, `close_reason` | leave blank |
| `profit_target_pct`, `stop_loss_multiple` | leave blank to use the function's defaults (50% / 2.0x), or set your own per-trade values |

**Valid `strategy` values** (anything else fails that row with a clear error
in the logs rather than silently mispricing it):

| `strategy` | option side | legs | `long_strike` |
|---|---|---|---|
| `cash_secured_put` | puts | 1 | leave blank |
| `covered_call` | calls | 1 | leave blank |
| `put_credit_spread` | puts | 2 | required |
| `call_credit_spread` | calls | 2 | required |

Note `premium_screener.py` itself only ever produces the first 3 of these
(it has no call-credit-spread strategy) -- `call_credit_spread` is supported
here only in case you ever want to manually track one you built yourself.

**Worked examples -- one row per strategy, exactly as it should look in the Sheet:**

| ticker | strategy | short_strike | long_strike | expiration | credit | entry_date | status | discord_message_id | profit_target_pct | stop_loss_multiple | closed_date | close_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `AAPL` | `cash_secured_put` | `220` | *(blank)* | `2026-08-14` | `1.35` | `2026-08-07` | `open` | *(blank)* | *(blank)* | *(blank)* | *(blank)* | *(blank)* |
| `MSFT` | `covered_call` | `480` | *(blank)* | `2026-08-14` | `2.10` | `2026-08-07` | `open` | *(blank)* | `40` | *(blank)* | *(blank)* | *(blank)* |
| `NVDA` | `put_credit_spread` | `170` | `165` | `2026-08-14` | `0.85` | `2026-08-07` | `open` | *(blank)* | *(blank)* | `2.5` | *(blank)* | *(blank)* |
| `SPY` | `call_credit_spread` | `560` | `565` | `2026-08-14` | `0.60` | `2026-08-07` | `open` | *(blank)* | *(blank)* | *(blank)* | *(blank)* | *(blank)* |

Notes on that example set:
- `short_strike`/`long_strike`/`credit` are plain numbers, no `$` sign.
- `expiration` and `entry_date` are `YYYY-MM-DD` (what `date.fromisoformat()` expects — anything else fails that row).
- MSFT shows a custom `profit_target_pct` of `40` (close at 40% of max profit instead of the function's default 50%); NVDA shows a custom `stop_loss_multiple` of `2.5` instead of the default 2.0 — both are optional per-row overrides, everything else here relies on the defaults.
- Once `position_monitor` flags one of these for closing, it fills in `discord_message_id` and `close_reason` itself and flips `status` to `pending_close` — you never type those two columns.
- After you react ✅ on the resulting Discord alert, it fills in `closed_date` and flips `status` to `closed`.

`position_monitor` picks up any row with `status = open` on its next run
(every 10 minutes, market hours) and starts watching it.

---

## Quick status checklist

- [x] Part A — local gcloud CLI installed, authenticated, project created, billing linked
- [x] Part B — Workload Identity Federation configured and verified
- [x] Part C — Terraform state bucket bootstrapped
- [ ] Part D — Discord webhook + bot created, secrets populated (currently placeholder values only)
- [ ] Part E — Google Sheet created, shared, sheet ID secret populated (currently placeholder value only)
