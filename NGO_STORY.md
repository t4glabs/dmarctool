# Why your organization's email needs a guardian — and what aikyam's Vigil does about it

*This is written for the people who run small nonprofits, grassroots movements, and community
organizations — not for technical staff. You don't need to know anything about computers to understand
this. You just need to know how your organization talks to the people who trust it.*

## The thing nobody tells you when you register a domain

When your organization got its own domain — `yourngo.org` — it probably felt like a milestone. A real
email address, a real website, a real name. It feels finished. It isn't.

Owning a domain does not automatically mean your email is trusted, or even safe. By default, almost
*anyone on the internet* can send an email that says it's from `yourngo.org`, to anyone — your donors,
your volunteers, your board, your beneficiaries — without your knowledge, without touching your systems,
and without you finding out until someone calls asking why you emailed them something strange.

This isn't a hypothetical. It's the single most common way small organizations get hurt online, and it's
almost invisible until the moment it happens.

## What it actually looks like, when it happens

Picture a long-time donor of your organization. Someone who has given, quietly and faithfully, for years.
One afternoon they get an email. It has your organization's name in the "From" line. It has your logo. It
sounds like you — maybe it even references a real campaign you ran last month, copied from your public
website. It asks them to "update their donation details" through a link, or to send an urgent
contribution to help with an emergency.

It is not from you. It never touched your inbox, your website, or your systems. Someone simply typed your
organization's name into the "From" field of an email and sent it — because, without protection, nothing
stops them.

Your donor doesn't know that. All they see is your name. If they act on it, they lose money, or hand over
their card details, to someone impersonating you. When they eventually find out it wasn't really you —
and often, they never do — the damage isn't just financial. It's trust. And trust, for an organization
that runs on donations and goodwill, is the entire business.

## Who actually gets hurt

This isn't only a "donor" problem. Everyone connected to your organization is a potential target, because
they all trust your name:

- **Donors and funders** — asked to "update payment details" or make an urgent gift, in your name.
- **Supporters and subscribers** — sent fake updates or links designed to steal their information.
- **Volunteers** — asked to "confirm" personal details, or sent malicious links disguised as a task
  request from your team.
- **Staff** — targeted with fake internal emails ("urgent, from the director") designed to trick them
  into a wire transfer or handing over passwords.
- **The people you serve** — your beneficiaries — sometimes the most vulnerable people in this entire
  chain, targeted with your name precisely because they trust it without question.

Every one of these relationships is what your organization exists to build. A single well-crafted fake
email can quietly poison all of them at once, and there is often no way to undo the damage once someone
has acted on it.

## The three guardians that actually stop this

There are three real, free, industry-standard technical protections that exist specifically to stop this.
None of them cost money. All of them were built for exactly this problem. Here's what each one actually
does, in plain terms:

**SPF (Sender Policy Framework)** — A public list, published for your domain, naming exactly which mail
servers are allowed to send email *as you*. When an email arrives claiming to be from `yourngo.org`, the
receiving mailbox can check: "was this actually sent from one of the servers this organization approved?"
If not, that's the first sign something is wrong.

**DKIM (DomainKeys Identified Mail)** — A digital signature, invisible to the reader, attached to every
real email your organization sends. It proves two things at once: that the email genuinely came from you,
and that nobody altered its contents in transit. Think of it as a wax seal on a letter — if the seal is
broken or missing, you know the letter isn't what it claims to be.

**DMARC (Domain-based Message Authentication, Reporting and Conformance)** — The policy that ties the
first two together and tells every mailbox provider in the world what to do when an email fails both
checks: ignore the failure (do nothing), send it to spam, or block it outright. DMARC also quietly sends
you reports of every attempt made in your name — including the ones that failed the checks — so you have
real, ongoing evidence of who's trying to impersonate you, not just a guess.

Without all three working together, your organization's name is effectively unguarded. Anyone can wear it.

## Why you can't just "turn on maximum protection" overnight

Once an organization understands this, the instinct is usually: *turn it all the way up, block
everything, right now.* This is exactly the mistake that causes real damage — and it's why this has to be
done carefully, one step at a time, not in one move.

Here's why: SPF and DKIM only cover the mail servers you've told them about. If your organization sends
email from more places than you remember — a newsletter tool, a donation platform, a shared inbox, an
old system nobody uses anymore but that still quietly sends receipts — and you switch DMARC straight to
"block everything that fails," you don't just block impersonators. You block **your own real emails**:
donation receipts, newsletters, event invitations, replies from your own staff. Your organization
effectively goes silent to everyone who matters, and you may not find out for weeks, because a blocked
email doesn't bounce back to tell you — it simply vanishes.

The right way is a **gradual ramp**: start by only watching and learning (nothing gets blocked yet, you
just collect the reports), confirm every legitimate source of mail your organization actually uses, then
slowly turn enforcement up — first sending suspicious mail to spam, only fully blocking it once you're
certain nothing genuine gets caught in the process. It's the same reasoning as tightening a new lock
gradually while making sure you still have your own key, rather than slamming the door shut and hoping for
the best.

This is exactly the part that requires ongoing attention, not a one-time setup — which is the core of
what a service like aikyam's actually does for you.

## The quieter threat: your own reputation

Spoofing isn't the only way trust gets damaged. There's a second, much quieter threat that has nothing to
do with anyone attacking you: **your own mailing list, left unmaintained.**

Every mailbox provider in the world — Gmail, Yahoo, Outlook, and everyone else — is constantly watching
*how* you send email, not just *what* you send. If you keep sending to addresses that don't work anymore,
that behaviour itself starts to look suspicious, even though nothing malicious is happening. It's treated
almost like a credit score for your domain: providers quietly track how "clean" and well-behaved your
sending has been over time, and a poor score means your genuine emails — including the ones your real
donors and supporters actually want — start landing in spam instead of the inbox, with no notice and no
warning.

Dead addresses build up on every mailing list, for entirely ordinary reasons: someone changes jobs and
their old work email stops existing; a personal account gets closed; a name was simply typed wrong years
ago and nobody noticed. There are a few different shades of this, and they matter differently:

- **Permanently dead addresses** — the address genuinely doesn't exist anymore, or the mailbox has been
  disabled for good. These are confirmed, safe to remove immediately, no ambiguity.
- **Temporary bounces** — a full inbox, a brief server hiccup — normally these resolve themselves within a
  send or two and need no action at all.
- **The address that's quietly been failing for months without ever formally bouncing** — this is the
  trickiest and most damaging kind, because nothing ever tells you outright that it's dead. It just sits
  on your list, silently dragging down your sender reputation every single time you send, month after
  month, until someone actually goes looking for it.

Cleaning these out isn't about being harsh with your own subscriber list — it's routine, necessary
maintenance, the same way you'd eventually clear an old contact who moved away with no forwarding address.
Left undone, it's one of the most common reasons a perfectly well-meaning organization's real newsletters
and receipts start disappearing into supporters' spam folders, with absolutely nothing to blame it on but
an unmaintained list.

## Beyond spoofing and reputation: the other quiet failure points

A handful of other things can just as quietly undermine the same trust, none of which most organizations
ever think to check:

- **Look-alike domains** — someone registering a domain deliberately designed to *resemble* yours
  (a single swapped letter, an extra hyphen) and using it to email your donors under a name that reads as
  almost-yours at a glance. DMARC alone can't catch this, because it isn't technically your domain being
  forged — it's a copy sitting right next to it.
- **Blocklists** — public "don't trust this sender" lists that mailbox providers consult automatically.
  Landing on one, even briefly, can silently send everything you send straight to spam until it's
  resolved.
- **An unsafe or compromised website** — if your website is ever flagged for hosting something malicious
  (often the result of an out-of-date plugin, not anything you did), visitors start seeing warning screens
  before they can even reach you — and it costs the same trust a scam email would.
- **Unencrypted delivery** — a newer, less well-known protection that stops mail sent *to* your
  organization from being intercepted or tampered with in transit, the same trust problem in reverse.
- **A domain about to expire** — if a domain registration lapses, your website and every email address on
  it stop working the same day, and someone else could register your name outright.

None of these show up in a normal inbox. They're the kind of failures an organization only discovers after
the damage is already done — which is exactly the gap a service like aikyam's Vigil exists to close.

## What aikyam's Vigil actually does for you, quietly, in the background

This is the part your organization never has to see or manage yourselves. Vigil watches everything above,
continuously, for every domain aikyam manages, so you don't have to learn any of it:

- **Sets up your email protection correctly and grows it gradually** — confirming every real source of
  your mail first, so protection gets stronger over time without ever blocking your own legitimate email.
- **Watches for anyone trying to impersonate you, right now** — and when it catches a real attempt, tells
  you plainly what the fake message looked like, roughly where it came from, and whether your protection
  actually stopped it.
- **Watches for copycat domains** registered to look like yours, so they get noticed early instead of
  quietly operating for months.
- **Keeps an eye on your sending reputation** everywhere you send mail from — catching a rising bounce or
  complaint problem long before it turns into a real spam-folder problem.
- **Reads Google's own verdict on your email**, straight from Google — the real spam rate for your domain,
  not a guess.
- **Cleans your mailing list for you** — finds the addresses that are truly dead, and the ones quietly
  acting dead without ever formally bouncing, and can email you one clear list of exactly what to remove
  and why.
- **Notices when subscribers stop opening your newsletters**, and can email you the exact list so you can
  decide whether to win them back or trim your list — either way, it's better than quietly mailing people
  who never read you.
- **Reviews your own newsletters** before they go out — checking subject lines and content for anything
  that reads like spam to an automated filter, and checking your "From" name is consistent and
  recognizable every time.
- **Warns you ahead of time if your domain registration is close to expiring**, since a lapsed domain can
  take your website and email down with it.
- **Checks your website hasn't been flagged unsafe**, and that mail sent to you is protected against
  interception — the kind of quiet failure point most organizations never think to check.
- **Reports back to you in plain language, on your own schedule** — no jargon, no technical terms, just
  what happened, what was fixed, and what's still being watched, sent straight to your inbox with a
  proper, easy-to-read report attached.

## What you actually receive

In practice, this means your organization gets a short, plain-language update by email — with a full,
clearly laid-out report attached — telling you, in your own words, how your email has been doing: what
was caught and stopped, what's still being kept an eye on, and whether anything needs your attention at
all. Most of the time, the honest answer is simply: *nothing does — it's being handled.*

And when something genuinely does need a small action on your end — like removing a batch of dead
addresses from your own subscriber list — you get a clear, one-click explanation of exactly what to remove
and why, instead of a wall of technical logs to interpret yourself.

## The bottom line

Your organization's name is one of its most valuable assets — it's what a donor trusts, what a funder
recognizes, what years of real work has built. Email is one of the easiest places for that name to be
quietly borrowed by someone with nothing to do with your mission. Protecting it isn't a technical luxury;
it's the same kind of ordinary care you'd give to your organization's bank account or its legal name — it
just happens to run on protocols most people have never heard of.

That's why aikyam built Vigil: so every organization it works with gets that protection watched over
continuously, without needing to become an expert in it themselves — so your name keeps the trust it has
earned, and your team can spend its time on the mission instead of the inbox.
