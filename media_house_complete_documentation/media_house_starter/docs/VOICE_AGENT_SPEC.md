# Voice Agent Specification — Hindi, Marathi and English

## 1. Purpose and scope

Provide a reliable conversational agent for approved workflows such as inbound appointment requests, service enquiries, meeting scheduling and simple status questions. It should sound clear, warm and natural; it must not deceive callers into thinking they are speaking to a human.

MVP should favor **inbound calls and permission-based customer communications**. Automated cold calling is disabled until provider reachability, consent, telecom requirements, channel policies and unit economics are confirmed.

## 2. Required capabilities

- Hindi, Marathi and English language selection and switching.
- Support for code-mixed speech (e.g., Marathi/Hindi with English product names) where benchmarked.
- Correct capture/confirmation of names, telephone numbers, dates, times and email addresses.
- Barge-in/interruptions, silence handling, noise recovery and repeat requests.
- Brief answers based only on an approved knowledge base.
- Read from the authorized calendar/CRM/order system; do not fabricate status.
- Schedule, reschedule or cancel only through an authoritative integration.
- Human transfer/callback option.
- Per-call cost/time limits, logs, provider status, error handling and fallbacks.
- AI disclosure and recording notice when applicable.
- Versioned scripts and consent/permission policy per tenant.

## 3. Sample opening

“नमस्कार, मैं [Business Name] का AI सहायक हूँ। मैं अपॉइंटमेंट या सामान्य जानकारी में मदद कर सकता/सकती हूँ। अगर आप चाहें, तो मैं आपकी बात टीम के किसी सदस्य तक पहुँचा सकता/सकती हूँ। आप हिंदी, मराठी या English में बात कर सकते हैं।”

Adapt the exact wording to the business and language. Do not falsely suggest a human is speaking.

## 4. Appointment flow

1. Identify business/clinic and purpose of the call.
2. Disclose AI identity and recording/transcription as applicable.
3. Ask preferred language.
4. Collect minimum necessary: name, callback number if required, service/provider preference, preferred date/time. Ask symptoms only if approved and necessary; default is not to collect.
5. Query the authoritative calendar/slot source.
6. Offer only actual available options.
7. Confirm selected slot, timezone/date, service/provider and contact channel.
8. Create booking; verify success response and booking ID.
9. Read back confirmation and send message only through a permitted, consented channel.
10. Provide cancel/reschedule/human fallback.
11. Record outcome and minimal metadata, not unnecessary transcript content.

### Failure behavior

If calendar unavailable, booking write fails, slot becomes taken, caller details are uncertain, or system is not confident: do not claim booking success. Offer callback/human transfer or record a request for manual confirmation.

## 5. Service-company lead handling

- Ask what they need help with, company name (if appropriate), preferred callback window and whether they want a meeting.
- Do not conduct aggressive qualification. Limit questions and allow the person to decline.
- Book only from the approved calendar.
- Explain the next step and avoid promising price/results before scope is assessed.
- Offer a human conversation quickly if the prospect asks detailed technical, contractual or pricing questions.

## 6. Conversation design

- Short, one-question-at-a-time turns.
- Natural but professional; no artificial over-familiarity or pressure.
- Confirm important numbers, dates, names and actions.
- Acknowledge uncertainty rather than guessing.
- Recognize “stop”, “don’t call”, “not interested”, “human”, “agent”, “कर्मचारी से बात”, “नको”, “बंद करा” and similar local-language requests; stop promotional conversation and record suppression where appropriate.
- Do not claim the agent has completed an external action until a verified provider response confirms it.

## 7. Medical safety boundaries

The appointment agent is administrative, not clinical.
- No diagnosis, medication or treatment guidance.
- No autonomous emergency assessment.
- No promise that a doctor will be available unless the calendar confirms it.
- If the caller describes urgent distress, use the clinic-approved escalation wording; recommend contacting local emergency services/clinician as appropriate, without delaying for a bot questionnaire.
- Staff must be able to review/override bookings and see exception queues.

## 8. Technical flow

`telephony provider → webhook/session gateway → audio stream → speech-to-text → dialogue/orchestration → approved tool call (calendar/CRM) → validated response → text-to-speech → call status webhook → minimal event log`.

Each component must have timeout and failure handling. Never send payment or booking mutations from unconstrained model text; use typed, validated tool calls with authorization.

## 9. Candidate speech providers

Evaluate provider options against:
- Indian inbound/outbound reachability and number availability;
- ability to use a lawful business number and caller ID;
- streaming audio latency and call stability;
- Hindi/Marathi/English and code-mixed benchmark results;
- telephony codec support and transcription quality on phone audio;
- call transfer/recording/consent functionality;
- data location, processing terms, retention and subprocessors;
- rate limits and usage costs;
- signed webhooks, monitoring and support.

Sarvam documents Indian-language STT/TTS and code-mixed usage: https://docs.sarvam.ai/api/getting-started/building-for-india. Exotel documents programmatic call management and webhooks: https://developer.exotel.com/docs/contact-center/overview, while its current India reachability/restrictions must be checked per use case: https://www.twilio.com/en-us/guidelines/in/voice is an example of how provider restrictions vary. Do not infer a provider is suitable from a generic feature page.

## 10. Evaluation set

Before live use, create at least 30 synthetic test cases per supported language, including:
- common requests;
- code switching;
- names and unusual spellings;
- date/time ambiguity;
- background noise;
- caller interrupting;
- repeated or conflicting details;
- unavailable slots;
- duplicate booking attempt;
- API outage;
- human-transfer request;
- refusal/opt-out;
- urgent medical statement (must follow safe escalation, no diagnosis);
- a question outside the approved knowledge base.

Measure task completion, field accuracy, false-confirmation rate, escalation rate, language quality, latency, abandonment, cost per successful interaction and human override rate. Target thresholds must be agreed from the pilot’s risk level; do not fabricate performance claims.

## 11. Minimum release gates

- Provider and legal review complete.
- AI/recording disclosures reviewed.
- Approved knowledge base and escalation script.
- Authoritative calendar integration verified.
- No false booking confirmation in tests.
- Human handoff works.
- Opt-out/suppression behavior tested.
- Logs do not expose unnecessary health/personal data.
- Per-call cost ceiling and kill switch implemented.
- Limited live pilot authorized by client and Media House.
