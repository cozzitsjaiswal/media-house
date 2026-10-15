# Research Sources and Evidence Register

**Checked:** 13 October 2026. Confirm each source again before committing money, publishing claims, or implementing a regulated workflow. This list provides market positioning and regulatory/technical starting points, not legal advice.

## Competitors / products

1. Zoho One — suite of business apps: https://www.zoho.com/one/
2. Odoo — business applications and pricing: https://www.odoo.com/ and https://www.odoo.com/pricing
3. Odoo Apps directory: https://apps.odoo.com/apps
4. HighLevel official pricing/support guide (plans, usage-based workflow/AI costs): https://help.gohighlevel.com/support/solutions/articles/155000001156-highlevel-pricing-guide
5. Petpooja restaurant POS feature page (billing/KOT, inventory, reports, orders, CRM/integrations): https://www.petpooja.com/poss
6. Practo Ray clinic management and plans: https://www.practo.com/providers/clinics/ray and https://www.practo.com/providers/clinics/ray/plans
7. Vyapar pricing/products: https://vyapar.com/pricing

## Indian language / voice infrastructure

8. Sarvam API overview: https://docs.sarvam.ai/
9. Sarvam models and language support: https://docs.sarvam.ai/api/getting-started/models
10. Sarvam guide to Indian-language/code-mixed speech and telephony: https://docs.sarvam.ai/api/getting-started/building-for-india
11. Exotel Contact Center API overview (programmatic call management/webhooks): https://developer.exotel.com/docs/contact-center/overview
12. Twilio India voice guidelines (example of provider-specific country restrictions; verify your chosen provider separately): https://www.twilio.com/en-us/guidelines/in/voice

## Messaging / privacy / communications

13. TRAI TCCCPR 2018: https://www.trai.gov.in/tcccpr
14. TRAI unsolicited commercial communication information: https://www.trai.gov.in/telecom/consumer-initiatives/unsolicited-commercial-communication
15. WhatsApp Business Messaging Policy (policy page surfaced with 23 September 2026 update; recheck before build/use): https://whatsappbusiness.com/policy/
16. Meta Terms for WhatsApp Business: https://www.whatsapp.com/legal/meta-terms-whatsapp-business
17. Meta for Developers WhatsApp Cloud API overview: https://developers.facebook.com/docs/whatsapp/cloud-api/overview
18. Google Gmail sender guidelines (SPF/DKIM, bulk sender and unsubscribe expectations): https://support.google.com/mail/answer/81126?hl=en-en
19. MeitY Act and Policies page, including DPDP Rules 2025: https://www.meity.gov.in/documents/act-and-policies
20. Government of India / ABDM Health Data Management Policy: https://www.india.gov.in/category/health-wellness/subcategory/health-care-promotion-products/details/health-data-management-policy-of-ayushman-bharat-digital-mission-abdm

## Data architecture / application security

21. Supabase securing data and RLS guidance (if Supabase is chosen; equivalent controls required for another stack): https://supabase.com/docs/guides/database/secure-data
22. Supabase RLS guide including testing allow/deny behaviors: https://supabase.com/docs/guides/database/postgres/row-level-security

## Findings that affect design

- Generic suites already cover many standard workflows; differentiate by a narrow, well-delivered outcome, integration and implementation support rather than breadth alone.
- Restaurant and clinic software have mature incumbents; start with adjacent workflows or integrations instead of a full replacement unless a proven gap exists.
- India-language speech services exist, but live telephony quality, Marathi performance, cost and legal/provider coverage need a benchmark with actual test calls.
- India has a framework for commercial communications; broad automated prospecting should not be enabled until the channel, registration, consent and provider setup are reviewed.
- WhatsApp policies/terms are date-sensitive; recheck templates, opt-in, user preferences, pricing and enforcement before enabling campaigns.
- Gmail sender authentication and unsubscribe requirements matter for deliverability and bulk commercial messages.
- DPDP rules and sector-specific health-data expectations must be checked for current applicability and commencement dates before production.
- RLS is not magic: grants, policies, tenant context and negative tests must be correct together.

## Evidence limitations

- This is not a numeric TAM/SAM/SOM report; no unsupported market-size number is provided.
- Vendor feature descriptions and client counts are self-reported unless independently validated.
- No local Maharashtra interviews, paid-pilot conversions, current provider quotations or actual Media House customer data have yet been added to this evidence register.
- Prices in this documentation are hypotheses; request current written quotations from vendors.
