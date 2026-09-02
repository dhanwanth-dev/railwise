# Where AI is used in Railwise (one-liners)

| Layer | AI used? | One-line why |
|---|---|---|
| **Ambiguous decline classifier** | Yes — logistic SGD | Same code `do_not_honor` means different things on SBI vs HDFC; model learns issuer + history patterns rules cannot capture. |
| **Recoverability score** | Yes — derived from classifier | Tells policy *how much* to trust a retry, not *whether* compliance allows it. |
| **Timing rank (payday / non-peak)** | Light — rule-based + signals | Picks the best legal retry window; not a black-box neural net. |
| **Issuer Health Monitor** | No — sliding-window stats | Counts technical declines per bank; fires when rate crosses a floor. Pure math, not ML. |
| **Mandate Vitality Scorer** | No — weighted rules | Scores mandate death risk from consecutive failures + days since last success. Interpretable thresholds. |
| **Hard constraint gate** | Never | NPCI attempt caps, PDN, token expiry, R0/R1 — compliance has zero model vote. |
| **Kill switch** | Never | Emergency stop; human override only. |

**Rule of thumb:** AI only votes when the decline reason is ambiguous and compliance already said retry is allowed.

---

# Real payment failure data — can we get it online?

## Short answer

**Real Razorpay production data: no** — it is merchant/customer PII and cannot be downloaded legally.

**Public proxies: yes** — but they are synthetic or aggregated, not Indian UPI AutoPay specifically.

## What exists publicly

| Source | What you get | Useful for Railwise? |
|---|---|---|
| [HuggingFace Nigerian card dataset](https://huggingface.co/datasets/electricsheepafrica/nigerian-banking-card-transactions) | ISO 8583 `issuer_response_code`, `decline_reason`, amounts | Partial — card declines only, not UPI, not India |
| [Zalingo synthetic CNP fraud set](https://www.opendatabay.com/) | `decline_reason_code`, auth signals | Partial — e-commerce CNP, not recurring mandates |
| [Payments & Risk ISO 8583 reference](https://paymentsandrisk.com/docs/reference/decline-codes/) | Code → retry? mapping | **High** — calibrates our rule taxonomy |
| NPCI monthly reports (public PDFs) | Issuer TD rates, UPI volume | **High** — calibrates issuer health baselines |
| PayPal sample decline CSV (public dev docs) | Decline reason text | Low — US-centric, not recurring |

## How it would leverage our model (if we used it)

1. **ISO code distribution** — train on real frequency of codes 05, 51, 91 instead of our guessed weights.
2. **Issuer-specific soft/hard ratios** — e.g. Nigerian data shows ~8% decline rate; we could calibrate `prior_hard_declines` feature priors.
3. **Amount × decline correlation** — large amounts may correlate with fraud-engine hard declines.

## What we do instead (honest approach)

1. **NPCI FY25 issuer TD rates** baked into `ISSUER_BASELINE_TD_RATES` and generator weights.
2. **ISO 8583 taxonomy** from Visa/Mastercard/RuPay public specs in `classify.py`.
3. **Domain-rule labels** in `train_model.py` — labels derived from payment expert rules, not random labels.
4. **Synthetic generator** calibrated to Indian rail mix (52% UPI, issuer market share).

## If Razorpay gave you real data in internship

You would replace `generator.py` labels with production webhook exports (anonymized), retrain the same logistic model, and compare accuracy on a held-out week. The **architecture would not change** — only the training distribution.

## Bottom line for judges

> "We cannot use real Razorpay customer data in a public repo. We calibrated synthetic data against NPCI issuer TD rates and ISO 8583 public taxonomy. The model architecture is production-shaped; the training distribution is domain-calibrated synthetic."
