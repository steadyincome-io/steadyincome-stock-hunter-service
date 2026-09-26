# Wheel Strategy Discord + Whop Launch Guide

Step-by-step setup for: free Discord channel (1 trade/month showcase) + paid
private channel (full trade access) via Whop, plus message templates for
posting trades.

---

## 1. Accounts to create

### 1.1 Gmail
- Create a **new, dedicated** Gmail (not your personal one) — e.g.
  `yourhandle.trades@gmail.com`. Use this for every account below so
  everything's tied to one recoverable identity, separate from your personal
  email/financial accounts.

### 1.2 X (Twitter)
- Sign up with the new Gmail.
- Handle: match your Discord/brand name so people can find both easily.
- Bio: state what you post (e.g. "Wheel strategy trades, posted weekly. Not
  financial advice.") + link to your Discord invite (and Whop checkout once
  live).
- Turn on 2FA immediately (via authenticator app, not SMS) — a hijacked
  account for a paid-content creator is a real target.

### 1.3 Whop (creator account)
- Sign up at whop.com with the new Gmail.
- Complete creator/seller onboarding (identity verification + payout method —
  bank account or similar). This step can take a few days to verify, so do it
  first before building anything else.
- Don't create your paid product yet — that happens after Discord is set up
  (step 4), since Whop's product needs the Discord server to link to.

---

## 2. Discord server setup

### 2.1 Create the server
- Discord → "+" → Create My Own → For a club or community.
- Name it to match your X handle/brand.
- Set a server icon/banner (can reuse whatever visual identity you use on X).

### 2.2 Basic server settings
- Server Settings → Safety Setup → enable the moderation defaults (verification
  level: at least "Medium").
- Server Settings → Roles → confirm `@everyone` has no special permissions
  beyond viewing the free channels (Whop will create/manage the paid role in
  step 3).

### 2.3 Create channels
Minimum structure:

| Channel | Type | Visibility |
|---|---|---|
| `#welcome` | text | Everyone |
| `#rules-disclaimer` | text | Everyone |
| `#free-monthly-trade` | text | Everyone (this is your 1-trade/month showcase) |
| `#general-chat` | text | Everyone |
| `#vip-trades` | text | **Paid role only** (set in step 3) |
| `#vip-chat` | text | **Paid role only** |

To restrict a channel: right-click the channel → Edit Channel → Permissions →
remove `@everyone`'s "View Channel" permission. You'll grant it back to the
paid role once Whop creates that role in step 3.

### 2.4 Post ground rules in `#rules-disclaimer`
Put your compliance disclaimer here (see section 5) before inviting anyone —
this is also where the "not financial advice" language lives so it's visible
to every member, free or paid.

---

## 3. Connect Whop and Discord

1. In Whop, create a new **Product**: Dashboard → Products → New Product.
   - Set pricing (monthly subscription).
   - Under "Access", choose **Discord** as the delivery method.
2. Whop will prompt you to **install the Whop bot** to your Discord server —
   authorize it (needs "Manage Roles" and "Manage Channels" permissions to
   function).
3. Whop auto-creates a role (e.g. "Whop Member" — you can rename it) tied to
   that specific product's subscription status.
4. Go back to `#vip-trades` and `#vip-chat` → Edit Channel → Permissions →
   grant "View Channel" to the **Whop-created role** (not `@everyone`).
5. In Whop's product settings, set the **cancellation behavior**:
   - Recommended (matches what you decided on): cancellation removes the
     paid role only, not the whole server — so a canceled subscriber keeps
     seeing `#free-monthly-trade` and can resubscribe later without needing a
     new invite.
   - Confirm this specific behavior in Whop's Discord integration settings
     before launch — this is the one detail worth testing with a dummy
     subscription first, since it directly affects what canceled users see.
6. Test end-to-end: create a test purchase (Whop supports test-mode
   checkouts), confirm the role gets assigned, confirm channel access appears,
   then cancel the test subscription and confirm the role (not server
   membership) gets removed.

---

## 4. Message templates for the Discord server

Keep these consistent so regulars know what to expect. Suggested variables
(pull straight from the Wheel Tracker/trade log): `TICKER`, `STRATEGY`,
`STRIKE`, `EXPIRATION`, `PREMIUM`, `QTY`, `COLLATERAL`, `OPEN_DATE`,
`CLOSE_DATE`, `NET_PL`, `ROI_PCT`.

### 4.1 New trade opened (paid channel)
```
🟢 NEW TRADE — {TICKER}
Strategy: {STRATEGY}
Strike: ${STRIKE}  |  Exp: {EXPIRATION}
Qty: {QTY} contract(s)
Premium collected: ${PREMIUM}
Collateral: ${COLLATERAL}
Opened: {OPEN_DATE}

Not financial advice — for informational/educational purposes only.
```

### 4.2 Trade closed (paid channel)
```
✅ CLOSED — {TICKER} {STRATEGY}
Strike: ${STRIKE}  |  Outcome: {CLOSE_REASON}
Net P/L: {NET_PL >= 0 ? "+" : ""}${NET_PL}
Held: {DAYS_HELD} days

Not financial advice.
```

### 4.3 Monthly free showcase trade (`#free-monthly-trade`)
```
📊 THIS MONTH'S FREE TRADE — {TICKER}
Strategy: {STRATEGY}  |  Strike: ${STRIKE}  |  Exp: {EXPIRATION}
Result: {CLOSE_REASON}, net {NET_PL >= 0 ? "+" : ""}${NET_PL}

Want to see every trade as it happens (not just one a month)? Full access →
{WHOP_CHECKOUT_LINK}

Not financial advice -- for informational/educational purposes only. Past
results don't guarantee future performance.
```

### 4.4 Welcome message (`#welcome`, posted automatically or pinned)
```
Welcome! 👋

This server tracks a real wheel-strategy (cash-secured puts + covered calls)
options trading account.

📊 #free-monthly-trade — one full trade breakdown, free, every month
🔒 #vip-trades — every trade, in real time, for subscribers

Read #rules-disclaimer before participating. Not financial advice.
```

### Automating this
Since your trades already live in the Wheel Tracker's database
(`wheel_trades.db`, via the local options API), a small script could pull a
trade record and fill these templates automatically instead of copy-pasting
by hand each time -- happy to build that next if useful, either as a
one-command generator or wired directly into Discord via a webhook.

---

## 5. Compliance disclaimer (put this in `#rules-disclaimer`, and in your X bio)

Suggested baseline text -- **not legal advice, consider having an actual
securities attorney review this before charging money for it**:

```
This server shares real trades from a personal options trading account for
informational and educational purposes only. Nothing posted here is
financial, investment, tax, or legal advice, and no content should be
construed as a recommendation to buy or sell any security. Options trading
involves substantial risk of loss and is not suitable for all investors.
Past performance shown here does not guarantee or predict future results.
Do your own research and consult a licensed financial advisor before making
any investment decisions.
```

Avoid, always: guarantees of profit, "you'll make your subscription fee
back," cherry-picked results without showing losers, and implying this is
personalized advice for any individual subscriber.

---

## 6. Suggested launch order (checklist)

- [ ] New Gmail created
- [ ] X account created, 2FA on, bio + link ready
- [ ] Whop creator account created, payout verification submitted
- [ ] Discord server created, channels set up, disclaimer posted
- [ ] Whop product created, bot installed, role permissions wired to
      `#vip-trades`/`#vip-chat`
- [ ] Cancellation behavior tested with a dummy subscription
- [ ] First free monthly trade posted in `#free-monthly-trade`
- [ ] First X post pointing to the Discord invite
