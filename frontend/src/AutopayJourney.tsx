import { useMemo, useState } from 'react'

type Phase =
  | 'checkout'
  | 'mandate_ok'
  | 'time_skip'
  | 'debit_fail'
  | 'fork'
  | 'walk'
  | 'outcome'

type WalkStep = {
  id: string
  title: string
  subtitle: string
  baseline: {
    headline: string
    detail: string
    badge: string
    tone: 'neutral' | 'warn' | 'bad'
  }
  railwise: {
    headline: string
    detail: string
    badge: string
    tone: 'neutral' | 'good' | 'ai' | 'rule'
    cards?: Array<{ label: string; value: string }>
  }
}

const PRODUCT = {
  name: 'ForgeCLI Pro',
  tagline: 'AI coding agent for production teams',
  priceInr: 999,
  period: 'month',
  features: ['Unlimited agent runs', 'Repo-aware refactoring', 'Priority model queue'],
  merchant: 'Forge Labs Pvt Ltd',
}

const FAILURE = {
  payment_id: 'pay_ForgeCLI_8xK2mQ',
  mandate_id: 'mandate_upi_sbi_4419',
  rail: 'UPI AutoPay',
  issuer: 'SBI',
  amount_paise: 99900,
  decline_code: 'insufficient_funds',
  iso: '51',
  attempt: 2,
  hours_since: 0.15, // ~9 minutes — too soon for UPI re-present
  consecutive: 1,
}

const WALK_STEPS: WalkStep[] = [
  {
    id: 'ingest',
    title: 'Failure arrives',
    subtitle: 'Webhook from Razorpay · subscription debit declined',
    baseline: {
      headline: 'Queue for retry in 1 hour',
      detail: 'Static template: every soft failure waits 60 minutes, then retries the same rail.',
      badge: 'TEMPLATE',
      tone: 'neutral',
    },
    railwise: {
      headline: 'Normalize event → PaymentFailureEvent',
      detail: 'Extract rail, issuer, ISO code, attempt count, PDN status, consecutive failures — one canonical schema for every downstream gate.',
      badge: 'INGEST',
      tone: 'rule',
      cards: [
        { label: 'Rail', value: 'UPI' },
        { label: 'Issuer', value: 'SBI' },
        { label: 'ISO', value: '51 NSF' },
        { label: 'Attempt', value: '2 / 4' },
      ],
    },
  },
  {
    id: 'classify',
    title: 'Classify the decline',
    subtitle: 'Is this soft, hard, regulatory, or ambiguous?',
    baseline: {
      headline: 'Treat as “retryable”',
      detail: 'Binary filter: if not stolen/lost card → retry. No issuer context. No recoverability score.',
      badge: 'BINARY',
      tone: 'warn',
    },
    railwise: {
      headline: 'ISO 51 → soft · recoverability 0.72',
      detail: 'Rules map insufficient funds as soft. No ML vote here — clear codes never go to the model. Score falls as attempts rise.',
      badge: 'RULES',
      tone: 'rule',
      cards: [
        { label: 'Kind', value: 'SOFT' },
        { label: 'Recoverability', value: '0.72' },
        { label: 'Source', value: 'rules' },
        { label: 'Confidence', value: '0.95' },
      ],
    },
  },
  {
    id: 'constraint_gap',
    title: 'UPI re-presentation gap',
    subtitle: 'Last attempt was 9 minutes ago · NPCI reconciliation window',
    baseline: {
      headline: 'Retry anyway in 60 minutes',
      detail: 'Ignores the 20-minute minimum re-present gap. Risk: double-debit during unsettled status.',
      badge: 'VIOLATION RISK',
      tone: 'bad',
    },
    railwise: {
      headline: 'Constraint UPI_COOLDOWN fires',
      detail: 'Forced action: delayed_retry. Minimum delay = remaining gap + buffer. Compliance overrides the 0.72 recoverability score.',
      badge: 'CONSTRAINT',
      tone: 'rule',
      cards: [
        { label: 'Gap actual', value: '9 min' },
        { label: 'Required', value: '≥ 20 min' },
        { label: 'Forced', value: 'delayed_retry' },
        { label: 'Priority', value: 'compliance > score' },
      ],
    },
  },
  {
    id: 'issuer',
    title: 'Issuer health check',
    subtitle: 'Cross-customer SBI technical decline rate in this batch',
    baseline: {
      headline: 'No issuer awareness',
      detail: 'All SBI mandates retry on the same schedule. If SBI is degraded, retries amplify load.',
      badge: 'BLIND',
      tone: 'warn',
    },
    railwise: {
      headline: 'SBI = DEGRADED · adaptive backoff',
      detail: 'Sliding-window TD monitor (not ML). When SBI spikes, delay widens so this mandate does not join a thundering herd.',
      badge: 'DEFENSE',
      tone: 'good',
      cards: [
        { label: 'TD rate', value: '14.2%' },
        { label: 'Baseline', value: '0.90%' },
        { label: 'Health', value: 'DEGRADED' },
        { label: 'Backoff', value: '+45 min' },
      ],
    },
  },
  {
    id: 'timing',
    title: 'Choose WHEN inside the legal window',
    subtitle: 'NSF + payday bias + NPCI non-peak hours',
    baseline: {
      headline: 'Fixed +60 minutes',
      detail: 'May land in peak UPI hours (10–13 / 17–21:30). No payday bias for salary credit.',
      badge: 'STATIC',
      tone: 'neutral',
    },
    railwise: {
      headline: 'Schedule: payday window · non-peak · avoid SBI rush',
      detail: 'Legal minimum delay already set by constraints. Timing rank picks next salary-day slot outside peak load — still inside NPCI OC/215A windows.',
      badge: 'POLICY',
      tone: 'good',
      cards: [
        { label: 'Action', value: 'delayed_retry' },
        { label: 'Delay', value: '36h 20m' },
        { label: 'Slot', value: 'Non-peak · day 1' },
        { label: 'Avoid', value: 'SBI 8–13h' },
      ],
    },
  },
  {
    id: 'execute',
    title: 'Execute the recovery action',
    subtitle: 'Bounded action enum only — no invented behaviours',
    baseline: {
      headline: 'Retry #2 fires early · fails again',
      detail: 'Same NSF context. Second failure burns attempt budget. No audit of why timing was chosen.',
      badge: 'FAILED AGAIN',
      tone: 'bad',
    },
    railwise: {
      headline: 'Delayed retry succeeds after salary credit',
      detail: 'Same mandate, legal window, payday-biased slot. Recovered ₹999. Full reason chain written to immutable audit.',
      badge: 'RECOVERED',
      tone: 'good',
      cards: [
        { label: 'Result', value: 'recovered' },
        { label: 'Amount', value: '₹999' },
        { label: 'Audit', value: '100%' },
        { label: 'Violations', value: '0' },
      ],
    },
  },
]

function formatInr(n: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n)
}

export default function AutopayJourney() {
  const [phase, setPhase] = useState<Phase>('checkout')
  const [autopayOn, setAutopayOn] = useState(false)
  const [walkIndex, setWalkIndex] = useState(0)
  const [revealed, setRevealed] = useState(0)
  const [timeSkipVisible, setTimeSkipVisible] = useState(false)

  const step = WALK_STEPS[walkIndex]
  const progress = useMemo(() => {
    const map: Record<Phase, number> = {
      checkout: 0,
      mandate_ok: 12,
      time_skip: 24,
      debit_fail: 36,
      fork: 48,
      walk: 48 + ((walkIndex + 1) / WALK_STEPS.length) * 40,
      outcome: 100,
    }
    return map[phase]
  }, [phase, walkIndex])

  function reset() {
    setPhase('checkout')
    setAutopayOn(false)
    setWalkIndex(0)
    setRevealed(0)
    setTimeSkipVisible(false)
  }

  function approveMandate() {
    if (!autopayOn) return
    setPhase('mandate_ok')
  }

  function startTimeSkip() {
    setPhase('time_skip')
    setTimeSkipVisible(false)
    requestAnimationFrame(() => {
      setTimeout(() => setTimeSkipVisible(true), 80)
    })
  }

  function advanceWalk() {
    if (revealed < 2) {
      setRevealed((r) => r + 1)
      return
    }
    if (walkIndex < WALK_STEPS.length - 1) {
      setWalkIndex((i) => i + 1)
      setRevealed(0)
      return
    }
    setPhase('outcome')
  }

  return (
    <div className="journey">
      <div className="journey-progress">
        <div className="journey-progress-bar" style={{ width: `${progress}%` }} />
      </div>

      {phase === 'checkout' && (
        <div className="journey-stage checkout-stage">
          <div className="checkout-shell">
            <div className="checkout-brand-bar">
              <span className="checkout-logo">ForgeCLI</span>
              <span className="checkout-secure">Secured by Razorpay</span>
            </div>

            <div className="checkout-body">
              <div className="checkout-product">
                <div className="product-visual" aria-hidden>
                  <div className="product-orb" />
                  <div className="product-terminal">
                    <span>$ forge agent --fix</span>
                    <span className="cursor-blink">▊</span>
                  </div>
                </div>
                <div>
                  <p className="product-eyebrow">Monthly subscription</p>
                  <h2>{PRODUCT.name}</h2>
                  <p className="product-tag">{PRODUCT.tagline}</p>
                  <ul className="product-features">
                    {PRODUCT.features.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="checkout-pay">
                <div className="price-line">
                  <span>Due today</span>
                  <strong>{formatInr(PRODUCT.priceInr)}</strong>
                </div>
                <div className="price-line muted-line">
                  <span>Then every month</span>
                  <span>{formatInr(PRODUCT.priceInr)} / {PRODUCT.period}</span>
                </div>

                <div className="pay-method">
                  <div className="pay-method-head">
                    <strong>UPI AutoPay</strong>
                    <span className="chip-live">Recommended</span>
                  </div>
                  <p className="pay-method-sub">One-time approval · debit on billing day · cancel anytime</p>
                  <div className="vpa-row">
                    <span className="vpa-label">Linked VPA</span>
                    <span className="vpa-value">arjun@oksbi</span>
                  </div>
                  <label className={`autopay-toggle ${autopayOn ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={autopayOn}
                      onChange={(e) => setAutopayOn(e.target.checked)}
                    />
                    <span className="autopay-box" />
                    <span>
                      I authorise <strong>{PRODUCT.merchant}</strong> to debit {formatInr(PRODUCT.priceInr)} monthly via UPI AutoPay
                    </span>
                  </label>
                </div>

                <button
                  className="btn-approve"
                  disabled={!autopayOn}
                  onClick={approveMandate}
                >
                  Approve AutoPay &amp; subscribe
                </button>
                <p className="checkout-fine">RBI e-mandate · Pre-debit notification 24h before each charge</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {phase === 'mandate_ok' && (
        <div className="journey-stage center-stage">
          <div className="mandate-card reveal">
            <div className="mandate-check">✓</div>
            <h2>Mandate active</h2>
            <p>UPI AutoPay authorised for {PRODUCT.name}</p>
            <div className="mandate-grid">
              <div><span>Mandate ID</span><strong>{FAILURE.mandate_id}</strong></div>
              <div><span>Bank</span><strong>State Bank of India</strong></div>
              <div><span>Amount</span><strong>{formatInr(PRODUCT.priceInr)} / month</strong></div>
              <div><span>Next debit</span><strong>Billing day + 30</strong></div>
            </div>
            <button className="btn-primary" onClick={startTimeSkip}>Continue</button>
          </div>
        </div>
      )}

      {phase === 'time_skip' && (
        <div className="journey-stage center-stage time-skip-stage">
          <div className={`time-skip ${timeSkipVisible ? 'show' : ''}`}>
            <p className="time-skip-eyebrow">Billing cycle</p>
            <h2 className="time-skip-title">30 days later</h2>
            <p className="time-skip-sub">Same calendar day · Razorpay presents the AutoPay debit</p>
            <button className="btn-primary" onClick={() => setPhase('debit_fail')}>
              Open debit attempt
            </button>
          </div>
        </div>
      )}

      {phase === 'debit_fail' && (
        <div className="journey-stage center-stage">
          <div className="fail-card reveal">
            <div className="fail-banner">PAYMENT FAILED</div>
            <h2>AutoPay debit declined</h2>
            <p className="fail-sub">{PRODUCT.name} · {formatInr(PRODUCT.priceInr)}</p>
            <div className="fail-meta">
              <div><span>Payment</span><strong>{FAILURE.payment_id}</strong></div>
              <div><span>Rail</span><strong>{FAILURE.rail}</strong></div>
              <div><span>Issuer</span><strong>{FAILURE.issuer}</strong></div>
              <div><span>Decline</span><strong>ISO {FAILURE.iso} · insufficient funds</strong></div>
              <div><span>Attempt</span><strong>#{FAILURE.attempt} of 4</strong></div>
              <div><span>Since last try</span><strong>9 minutes</strong></div>
            </div>
            <p className="fail-note">
              Soft decline — customer likely gets salary soon. The next decision decides whether you recover revenue or burn the UPI attempt budget.
            </p>
            <button className="btn-primary" onClick={() => setPhase('fork')}>
              Compare recovery paths
            </button>
          </div>
        </div>
      )}

      {phase === 'fork' && (
        <div className="journey-stage">
          <div className="fork-intro reveal">
            <h2>Two engines. One failure.</h2>
            <p>Walk each gate side by side. Baseline follows a static hourly template. Railwise runs the constraint-first lattice you shipped.</p>
          </div>
          <div className="fork-panels">
            <div className="fork-panel baseline">
              <h3>Baseline</h3>
              <p>Static hourly retry · no issuer signal · no NPCI gap check</p>
              <ul>
                <li>Retry in 60 minutes</li>
                <li>Same rail, same schedule</li>
                <li>No audit of constraint hits</li>
              </ul>
            </div>
            <div className="fork-panel railwise">
              <h3>Railwise</h3>
              <p>Normalize → classify → constraints → issuer health → timing → execute</p>
              <ul>
                <li>Compliance before recoverability</li>
                <li>Issuer-aware backoff</li>
                <li>Immutable reason chain</li>
              </ul>
            </div>
          </div>
          <div className="fork-cta">
            <button
              className="btn-primary"
              onClick={() => {
                setPhase('walk')
                setWalkIndex(0)
                setRevealed(0)
              }}
            >
              Begin walkthrough
            </button>
          </div>
        </div>
      )}

      {phase === 'walk' && step && (
        <div className="journey-stage walk-stage">
          <div className="walk-header">
            <div>
              <p className="walk-step-count">Gate {walkIndex + 1} of {WALK_STEPS.length}</p>
              <h2>{step.title}</h2>
              <p className="walk-sub">{step.subtitle}</p>
            </div>
            <div className="walk-dots">
              {WALK_STEPS.map((s, i) => (
                <span key={s.id} className={`walk-dot ${i < walkIndex ? 'done' : ''} ${i === walkIndex ? 'active' : ''}`} />
              ))}
            </div>
          </div>

          <div className="walk-columns">
            <div className={`walk-col baseline-col ${revealed >= 1 ? 'revealed' : 'dimmed'}`}>
              <div className="col-label">Baseline</div>
              {revealed >= 1 ? (
                <div className={`path-card tone-${step.baseline.tone} reveal`}>
                  <span className="path-badge">{step.baseline.badge}</span>
                  <h3>{step.baseline.headline}</h3>
                  <p>{step.baseline.detail}</p>
                </div>
              ) : (
                <div className="path-placeholder">Waiting…</div>
              )}
            </div>

            <div className={`walk-col railwise-col ${revealed >= 2 ? 'revealed' : 'dimmed'}`}>
              <div className="col-label accent">Railwise</div>
              {revealed >= 2 ? (
                <div className={`path-card tone-${step.railwise.tone} reveal`}>
                  <span className="path-badge">{step.railwise.badge}</span>
                  <h3>{step.railwise.headline}</h3>
                  <p>{step.railwise.detail}</p>
                  {step.railwise.cards && (
                    <div className="path-metrics">
                      {step.railwise.cards.map((c) => (
                        <div key={c.label} className="path-metric">
                          <span>{c.label}</span>
                          <strong>{c.value}</strong>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="path-placeholder">Reveal next</div>
              )}
            </div>
          </div>

          <div className="walk-actions">
            <button className="btn-ghost-dark" onClick={() => { setPhase('fork'); setRevealed(0) }}>Back</button>
            <button className="btn-primary" onClick={advanceWalk}>
              {revealed === 0 && 'Show baseline decision'}
              {revealed === 1 && 'Show Railwise decision'}
              {revealed === 2 && (walkIndex < WALK_STEPS.length - 1 ? 'Next gate' : 'See outcomes')}
            </button>
          </div>
        </div>
      )}

      {phase === 'outcome' && (
        <div className="journey-stage">
          <div className="outcome-intro reveal">
            <h2>Same customer. Different recovery.</h2>
            <p>Baseline burned an early retry and failed again. Railwise waited for a legal, payday-biased slot — and collected.</p>
          </div>
          <div className="outcome-grid">
            <div className="outcome-card bad">
              <h3>Baseline</h3>
              <div className="outcome-stat">₹0 recovered</div>
              <ul>
                <li>Retry fired inside UPI gap window risk</li>
                <li>Attempt budget: 2 → 3 wasted on same NSF context</li>
                <li>No issuer backoff · no payday timing</li>
                <li>Hard path toward attempt exhaustion</li>
              </ul>
            </div>
            <div className="outcome-card good">
              <h3>Railwise</h3>
              <div className="outcome-stat">₹999 recovered</div>
              <ul>
                <li>UPI_COOLDOWN forced legal delay</li>
                <li>SBI degraded → adaptive backoff applied</li>
                <li>Payday + non-peak slot · debit succeeded</li>
                <li>0 violations · full audit trail</li>
              </ul>
            </div>
          </div>
          <div className="outcome-proof reveal">
            <div className="proof-item">
              <strong>Why this recovers more</strong>
              <p>Waiting for salary credit on NSF is not “slower” — it is higher expected recovery with zero scheme risk.</p>
            </div>
            <div className="proof-item">
              <strong>Where AI did not vote</strong>
              <p>ISO 51 classification and UPI gap are rules. AI only ranks timing among already-legal slots and handles ambiguous codes elsewhere.</p>
            </div>
            <div className="proof-item">
              <strong>What a reviewer can verify</strong>
              <p>Open Sandbox Lab on this failure shape, or run Edge Cases → UPI cooldown. Same constraint code fires in the live engine.</p>
            </div>
          </div>
          <div className="fork-cta">
            <button className="btn-secondary" onClick={reset}>Replay journey</button>
          </div>
        </div>
      )}
    </div>
  )
}
