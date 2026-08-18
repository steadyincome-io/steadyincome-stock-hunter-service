# The 5 Discord/Sheet secret containers this project used (discord-webhook-url,
# discord-bot-token, discord-channel-id, discord-user-id, google-sheet-id) --
# decommissioned along with position_monitor itself. See functions.tf /
# infra/MANUAL_SETUP.md "Decommissioning" for the teardown record and the
# real production incident these secrets were involved in (Terraform
# auto-recreating a placeholder version and shadowing the real value) --
# worth reading before ever re-adding automated secret-version management
# to this file in the future.
