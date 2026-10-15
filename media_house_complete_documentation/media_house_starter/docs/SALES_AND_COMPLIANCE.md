# Sales, Outreach and Compliance Guardrails

**Purpose:** Enable a useful prospect-to-client workflow without creating spam, telecom, privacy, platform, or reputational risk. This is an engineering/control checklist, not legal advice. Review requirements for each state, communication channel and target country before live operation.

## 1. Lead research

- Use official business directories, company websites, opt-in directories, licensed datasets or other permitted sources.
- Preserve source URL, retrieval date, purpose, and verification status.
- Only collect fields necessary for B2B qualification.
- Do not bypass access controls, CAPTCHAs, logins, rate limits, robots/terms restrictions or paid data access.
- Do not scrape personal profiles/contact details at scale or infer personal contact information from patterns.
- Do not fabricate email addresses, phone numbers, titles, problems or buying intent.
- Keep company facts separate from AI-generated hypotheses.
- Give each prospect a confidence score and evidence for fit.

## 2. Outreach policy

### Required states

`researched → approved draft → sent via approved channel → reply/response status → follow-up decision → suppressed/closed`.

The system must block sending if:
- contact is in the suppression/opt-out list;
- channel permission/legal basis is unresolved;
- message lacks required identity or disclosure;
- provider credential or sender setup is not verified;
- rate/spend limit is exceeded;
- campaign has not been approved by a human.

### Human approval

MVP does not auto-send outbound communications. The system drafts, queues and records; a human reviews the audience, purpose, content and channel before send. Turn on automation only after a jurisdiction/channel-specific review and explicit owner approval.

### Email

- Do not send bulk email from a personal Gmail account or unconfigured domain.
- Set up authenticated sending (SPF/DKIM; DMARC, especially for bulk) and monitored bounce/complaint rates.
- Use truthful sender identity, relevant subject line, appropriate business purpose and an easy opt-out where required.
- Honor suppression at send time and across all sequences.
- Do not use purchased lists without verifying lawful sourcing, terms and recipient expectations.
- Google’s official Gmail sender guidance sets authentication and bulk-sender expectations: https://support.google.com/mail/answer/81126?hl=en-en.

### India voice calls and SMS

- TRAI’s TCCCPR framework addresses unsolicited commercial communication and recipient preferences: https://www.trai.gov.in/tcccpr.
- Do not run automated promotional calls/SMS from ordinary personal numbers or assume B2B means exempt.
- Before live campaign use, obtain specialist advice on principal entity/registered telemarketer obligations, number series, consent/preferences, headers/templates and provider requirements as applicable.
- Start with manually reviewed, tightly targeted business outreach and inbound/permission-based use cases.

### WhatsApp

- Use the official WhatsApp Business Platform/API and a verified business setup where appropriate.
- Follow current WhatsApp Business Messaging Policy and Terms: https://whatsappbusiness.com/policy/ and https://www.whatsapp.com/legal/meta-terms-whatsapp-business.
- Business-initiated messages must follow current template, consent/opt-in, quality and policy rules. Do not blast scraped contacts or automate the consumer WhatsApp app in a way that circumvents platform controls.
- Record the opt-in source, timestamp, permitted message purpose, and opt-out.
- Reconfirm current conversation windows, template requirements, fees and category rules before launch; these can change.

## 3. AI voice disclosure and human-like language

The voice agent may use natural pacing, clear pronunciation and culturally appropriate Hindi/Marathi/English. It must not claim or imply that it is a human employee. It should introduce itself as the business’s AI assistant, or otherwise disclose clearly as required by provider/jurisdiction. If asked, it must directly say it is AI. Never use voice cloning of a real person without documented rights and authorization.

At call start (example): “नमस्कार, मैं [Business] का AI फोन सहायक बोल रहा/रही हूँ। मैं अपॉइंटमेंट/जानकारी में मदद कर सकता/सकती हूँ। आप चाहें तो मैं कर्मचारी से बात करवा सकता/सकती हूँ।”

## 4. Recording and transcription

- Do not record by default unless business need and lawful basis are assessed.
- If recording/transcription is used, disclose it before capture and obtain consent where required.
- Offer a non-recorded/human alternative when feasible.
- Limit access, retention and reuse; do not train general models on client calls without specific authorization and valid legal basis.
- Store provider call IDs and minimal outcomes where possible instead of raw audio.

## 5. Calendar and booking integrity

- An appointment agent must read from the authoritative calendar/booking system.
- Never make up available slots or mark an appointment confirmed until the booking API/system confirms it.
- Use idempotency or duplicate detection to prevent double bookings.
- Confirm changes to the caller and send only approved notifications through a permitted channel.

## 6. Patient/health-related workflows

- Treat appointment details linked to a person as sensitive and minimize collection.
- Do not diagnose, recommend treatment, prioritize patients clinically or provide emergency advice beyond a client-approved protocol.
- For urgent symptoms, follow the client-approved escalation script and direct the caller to appropriate emergency/clinical assistance; do not triage autonomously.
- Do not store detailed symptoms by default. Do not build an EMR/EHR or process records until privacy/security obligations, data controller/processor roles, retention, backups, access logs and contractual responsibilities have been reviewed.
- Review the ABDM Health Data Management Policy where relevant: https://www.india.gov.in/category/health-wellness/subcategory/health-care-promotion-products/details/health-data-management-policy-of-ayushman-bharat-digital-mission-abdm.

## 7. Data protection

- Review India's DPDP Act and the DPDP Rules 2025 and the applicable commencement/transition provisions before production: https://www.meity.gov.in/documents/act-and-policies.
- For users or clients outside India, separately assess local data protection, telemarketing, electronic communications, recording and consumer laws.
- Document purpose, data categories, recipients/processors, retention and deletion for each workflow.
- Collect explicit, affirmative consent where required; do not infer consent from public listing alone.

## 8. AI sales behavior

Allowed: answer questions using approved company/service facts, ask relevant qualification questions, suggest next steps, book within allowed windows, create a proposal draft, summarize objections.

Not allowed: fabricate client outcomes, guarantees, testimonials, prices, availability, certifications or urgency; pressure vulnerable people; hide fees; claim an integration works without testing; override opt-out; continue after a clear refusal; make legal/medical/financial assurances beyond approved content.

## 9. Global outreach

Before any new country/campaign, create a country+channel approval checklist covering legal basis, do-not-call registries, local telecom rules, email/privacy law, recording disclosure, time-zone/quiet-hour rules, sender registration, data residency, taxes and the provider’s terms. Until approved, keep the channel disabled.

## 10. Incident handling

If a complaint, accidental message, data exposure, wrong booking or harmful call occurs:
1. Stop the campaign/integration if needed.
2. preserve relevant logs securely;
3. notify the designated owner;
4. assess affected people/data and contractual/legal duties;
5. correct and test the cause;
6. resume only after explicit approval.
