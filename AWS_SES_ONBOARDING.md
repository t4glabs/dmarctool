# Onboarding a new domain/identity into AWS SES (for DMARCTool)

Internal runbook -- read this before adding a new SES identity, so you don't have to ask again.
Written 2026-09-19, values below confirmed live against the real AWS account at that time.

## The architecture (why this works the way it does)

DMARCTool has no per-domain read API from SES itself -- the only way it learns about bounces,
complaints, opens, clicks, deliveries, and rejects is by draining an SQS queue that one shared SNS
topic feeds. **Every domain's configuration set publishes to the SAME topic and queue.** You never
create a new topic or queue for a new domain -- only a new configuration set, pointed at the existing
topic.

- SNS topic: `arn:aws:sns:ap-south-1:905418021415:ses-events`
- SQS queue: `https://sqs.ap-south-1.amazonaws.com/905418021415/ses-events-queue`
- Region: `ap-south-1`
- The topic is already subscribed to the queue -- don't touch that part.

DMARCTool matches an incoming event's configuration-set name back to a tracked domain by a strict
naming rule (`app/ses_events.py::_config_set_domain_map`):

> **configuration set name = the domain name with every `.` replaced by `-`, nothing else.**

Examples already in use: `pattic.org` -> `pattic-org`, `aikyam.school` -> `aikyam-school`,
`aikyam.space` -> `aikyam-space`, `aikyamjobs.org` -> `aikyamjobs-org`.

**Get this wrong and events silently vanish** -- DMARCTool won't error, it'll just never attribute
anything to that domain. This already happened once: a configuration set was named `aikyam-fellows`
for `aikyamfellows.org` (should have been `aikyamfellows-org`) -- looked reasonable, followed the
naming rule loosely instead of mechanically, and broke it.

## Steps for a new identity (do this every time)

1. **Verify the identity in SES** (domain and/or a specific email address) -- you already know this part.

2. **Create a configuration set** named EXACTLY `<domain-with-dots-as-hyphens>` -- e.g. for
   `newdomain.org`, the configuration set must be named `newdomain-org`. No abbreviating, no extra
   hyphens, no dropping the TLD suffix. If you're adding an email-address identity (like
   `hello@aikyamfellows.org`) that shares a domain already being tracked, it can point at that
   domain's existing configuration set -- you don't need a second one just for the email-address
   identity.

3. **Add an event destination to that configuration set**, pointing at the existing shared topic:
   - SES console -> Configuration sets -> (your new config set) -> Event destinations -> Add destination
   - Destination type: **Amazon SNS**
   - Topic: select the existing `ses-events` topic (`arn:aws:sns:ap-south-1:905418021415:ses-events`)
     -- do NOT create a new topic
   - Name the destination `to-sqs` (matches every other domain's destination name -- purely for your
     own consistency, DMARCTool doesn't care about this name)
   - Event types to enable (matches every other domain): **Bounce, Complaint, Delivery, Reject, Open,
     Click**
   - **Watch out for "Delivery" vs "Delivery Delay"** -- they're separate checkboxes right next to each
     other in the console, and only plain "Delivery" is what `EVENT_TO_COUNTER` in `ses_events.py`
     actually counts (`"delivery": "delivered"`). "Delivery Delay" and "Rendering Failure" are different
     SES notification types the code doesn't use at all -- this already happened once for real
     (`aikyam-space` was set up with Delivery Delay + Rendering Failure checked but plain Delivery
     unchecked, so it would never have counted a single delivered message). Double check the exact box
     labeled "Delivery" is ticked, not just something starting with "Delivery".

4. **Set the configuration set as the identity's default**, so mail actually sent through this
   identity gets tagged with it automatically (SES console -> Identities -> your identity ->
   Configuration set tab -> set the default). Do this for BOTH a domain identity and any email-address
   identity under it if they're meant to share the same configuration set.

5. **Nothing else to touch** -- no new SQS queue, no new SNS topic, no new IAM permissions, no
   DMARCTool code/config change (as long as step 2's naming is exact, `app/ses_events.py` picks up
   the new configuration set automatically the next time it runs, no restart needed).

## How to confirm it worked

- Send (or wait for) a real message through the new identity.
- Within a few hours (background sweep runs every 6h, or trigger "Refresh now" on the dashboard),
  check that domain's page in DMARCTool -- known senders / event counts should start showing real data.
- If nothing shows up after a real send, the most likely cause is the naming mismatch in step 2 --
  double check the configuration set name character-for-character against the domain.

## Fixing a wrongly-named configuration set (can't rename in AWS SES)

AWS SES configuration sets can't be renamed. If you named one wrong (like `aikyam-fellows` instead of
`aikyamfellows-org`):

1. Create a new configuration set with the correct name.
2. Add the same event destination (step 3 above) to it.
3. Change the affected identity/identities' default configuration set to the new one (step 4).
4. The old, wrongly-named configuration set is now unused -- safe to delete once nothing points to it,
   or just leave it as harmless clutter.
