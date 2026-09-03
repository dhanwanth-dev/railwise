import { useCallback, useEffect, useMemo, useState } from 'react'

const API = '/api'

type Phase = 'checkout' | 'mandate' | 'timeskip' | 'failure' | 'log' | 'outcome'
type PayMethod = 'upi' | 'card'
type Scenario = 'nsf_early_retry' | 'ambiguous_sbi' | 'token_expired'

type Toggles = {
  use_ml_model: boolean
  use_compliance_blocks: boolean
  use_issuer_health: boolean
  use_mandate_vitality: boolean
  use_timing_ai: boolean
}

type Decision = {
  action: string
  delay_minutes?: number | null
  classification: {
    decline_kind: string
    recoverability: number
    confidence: number
    source: string
    reason_codes?: string[]
    feature_importance?: Record<string, number>
  }
  constraint_hits: Array<{ code: string; message: string; forced_action?: string | null }>
  reason_chain: string[]
  execution_result?: string
  recovered_amount_paise?: number
  issuer_health_level?: string
  mandate_vitality_level?: string
  target_rail?: string | null
}

type LogCommit = {
  id: string
  kind: string
  title: string
  body: string
  guideline?: string | null
  ai_used: boolean
  forced_action?: string | null
  feature_importance?: Record<string, number>
  reason_chain?: string[]
}

type JourneyResult = {
  event: Record<string, unknown>
  baseline: Decision
  full_railwise: Decision
  your_config: Decision
  action_changed: boolean
  constraints_changed?: boolean
  railwise_log: LogCommit[]
  baseline_log: LogCommit[]
  failure_feed: Array<{
    decision_id: string
    payment_id: string
    rail: string
    issuer_bank?: string
    decline_code: string
    decline_iso_code?: string
    amount_paise: number
    action: string
    recoverability: number
    decline_kind: string
    attempt_number?: number
    classification_source?: string
    execution_result?: string
  }>
  batch_metrics?: {
    railwise?: { soft_recovery_rate: number; recovered_paise: number; hard_decline_wasted_retries: number; upi_cooldown_violations: number }
    baseline?: { soft_recovery_rate: number; recovered_paise: number; hard_decline_wasted_retries: number; upi_cooldown_violations: number }
    lift?: { soft_recovery_rate_delta: number; recovered_paise_delta: number }
  }
}

const PRODUCT = {
  name: 'ForgeCLI Pro',
  tagline: 'AI coding agent for production teams',
  priceInr: 999,
  features: ['Unlimited agent runs', 'Repo-aware refactoring', 'Priority model queue'],
  merchant: 'Forge Labs Pvt Ltd',
}

const STEPS: Array<{ id: Phase; label: string }> = [
  { id: 'checkout', label: 'Checkout' },
  { id: 'mandate', label: 'Mandate' },
  { id: 'timeskip', label: 'Billing' },
  { id: 'failure', label: 'Failure' },
  { id: 'log', label: 'Decision log' },
  { id: 'outcome', label: 'Outcome' },
]

const DEFAULT_TOGGLES: Toggles = {
  use_ml_model: true,
  use_compliance_blocks: true,
  use_issuer_health: true,
  use_mandate_vitality: true,
  use_timing_ai: true,
}

const TOGGLE_META: Array<{ key: keyof Toggles; label: string; hint: string }> = [
  { key: 'use_ml_model', label: 'ML', hint: 'Ambiguous ISO 05 classifier' },
  { key: 'use_compliance_blocks', label: 'Compliance', hint: 'NPCI / RBI hard ceilings' },
  { key: 'use_issuer_health', label: 'Issuer', hint: 'TD spike backoff' },
  { key: 'use_mandate_vitality', label: 'Vitality', hint: 'Dead-mandate dunning' },
  { key: 'use_timing_ai', label: 'Timing', hint: 'Legal slot ranking' },
]

const DECLINE_COPY: Record<string, string> = {
  insufficient_funds: 'Your bank declined this payment due to insufficient funds.',
  do_not_honor: 'The issuing bank declined this payment (do not honor).',
  token_expired: 'The saved card token has expired. Customer must re-tokenize.',
}

function inr(paise: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
}

function actionClass(action: string) {
  return `action-badge action-${action}`
}

function stepIndex(phase: Phase) {
  return STEPS.findIndex((s) => s.id === phase)
}

function HackerTypeReveal({ onDone }: { onDone: () => void }) {
  const target = '30 DAYS LATER'
  const [text, setText] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    let i = 0
    const id = window.setInterval(() => {
      i += 1
      setText(target.slice(0, i))
      if (i >= target.length) {
        window.clearInterval(id)
        setDone(true)
      }
    }, 110)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="hacker-reveal">
      <div className="hacker-frame">
        <div className="hacker-chrome">
          <span /><span /><span />
          <em>billing_cycle.exe</em>
        </div>
        <div className="hacker-body">
          <p className="hacker-prompt">&gt; advance_calendar --days 30</p>
          <h2 className="hacker-text">
            {text.split('').map((ch, idx) => (
              <span key={idx} className={ch === ' ' ? 'hacker-space' : 'hacker-char'}>
                {ch === ' ' ? '\u00A0' : ch}
              </span>
            ))}
            <span className="hacker-cursor">▌</span>
          </h2>
          {done && <p className="hacker-sub reveal">Same calendar day · AutoPay presentation queued</p>}
        </div>
      </div>
      {done && <button className="btn-primary" onClick={onDone}>Open debit attempt</button>}
    </div>
  )
}

function PaymentFailureCard({
  title,
  amountPaise,
  rail,
  issuer,
  decline,
  iso,
  paymentId,
  attempt,
  status,
  customerId,
  createdAt,
  mandateId,
}: {
  title: string
  amountPaise: number
  rail: string
  issuer: string
  decline: string
  iso?: string
  paymentId: string
  attempt: string | number
  status: string
  customerId?: string
  createdAt?: string
  mandateId?: string
}) {
  const reason = DECLINE_COPY[decline] || 'The payment could not be completed.'
  return (
    <div className="rzp-fail-card">
      <div className="rzp-fail-top">
        <div className="rzp-fail-brand">
          <span className="rzp-mark">Razorpay</span>
          <span className="rzp-dash-label">Payments</span>
        </div>
        <span className="rzp-fail-status">{status}</span>
      </div>
      <div className="rzp-fail-banner">
        <strong>Payment failed</strong>
        <p>{reason}</p>
      </div>
      <div className="rzp-fail-body">
        <p className="rzp-fail-merchant">{title}</p>
        <div className="rzp-fail-amount">{inr(amountPaise)}</div>
        <div className="rzp-fail-grid">
          <div><span>Payment ID</span><strong>{paymentId}</strong></div>
          <div><span>Customer</span><strong>{customerId || '—'}</strong></div>
          <div><span>Method</span><strong>{rail.toUpperCase()}</strong></div>
          <div><span>Issuer</span><strong>{issuer.toUpperCase()}</strong></div>
          <div><span>Attempt</span><strong>#{attempt}</strong></div>
          <div><span>Created</span><strong>{createdAt ? createdAt.replace('T', ' ') : '—'}</strong></div>
          <div className="span-2">
            <span>Error</span>
            <strong>{decline}{iso ? ` · ISO ${iso}` : ''}</strong>
          </div>
          {mandateId && (
            <div className="span-2">
              <span>Mandate / Token</span>
              <strong>{mandateId}</strong>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function CommitRow({
  commit,
  index,
  active,
  onSelect,
}: {
  commit: LogCommit
  index: number
  active: boolean
  onSelect: () => void
}) {
  return (
    <button type="button" className={`commit-row ${active ? 'active' : ''} kind-${commit.kind}`} onClick={onSelect}>
      <div className="commit-rail">
        <span className="commit-dot" />
        {index < 99 && <span className="commit-line" />}
      </div>
      <div className="commit-main">
        <div className="commit-meta">
          <code>{commit.id}</code>
          <span className={`commit-kind ${commit.ai_used ? 'ai' : 'rules'}`}>
            {commit.ai_used ? 'AI' : commit.kind.toUpperCase()}
          </span>
        </div>
        <strong>{commit.title}</strong>
        <p>{commit.body}</p>
      </div>
    </button>
  )
}

export default function AutopayJourney() {
  const [phase, setPhase] = useState<Phase>('checkout')
  const [payMethod, setPayMethod] = useState<PayMethod>('upi')
  const [scenario, setScenario] = useState<Scenario>('nsf_early_retry')
  const [consent, setConsent] = useState(false)
  const [toggles, setToggles] = useState<Toggles>(DEFAULT_TOGGLES)
  const [journey, setJourney] = useState<JourneyResult | null>(null)
  const [commitIdx, setCommitIdx] = useState(0)
  const [side, setSide] = useState<'railwise' | 'baseline'>('railwise')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runJourney = useCallback(async (rail: PayMethod, scen: Scenario, t: Toggles) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/journey/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rail, scenario: scen, ...t, include_batch_feed: true }),
      })
      if (!res.ok) throw new Error(`Engine pass failed (${res.status})`)
      const data: JourneyResult = await res.json()
      setJourney(data)
      return data
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Engine unavailable. Start the API on :8000.')
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (phase === 'log' || phase === 'outcome' || phase === 'failure') {
      runJourney(payMethod, scenario, toggles).then((data) => {
        if (data && phase === 'log') {
          setCommitIdx((i) => Math.min(i, Math.max(0, (data.railwise_log?.length || 1) - 1)))
        }
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toggles])

  function reset() {
    setPhase('checkout')
    setConsent(false)
    setJourney(null)
    setCommitIdx(0)
    setToggles(DEFAULT_TOGGLES)
    setSide('railwise')
    setError(null)
  }

  async function openFailure() {
    setPhase('failure')
    await runJourney(payMethod, scenario, toggles)
  }

  async function openLog() {
    setPhase('log')
    setCommitIdx(0)
    setSide('railwise')
    if (!journey) await runJourney(payMethod, scenario, toggles)
  }

  const progress = useMemo(() => {
    const map: Record<Phase, number> = {
      checkout: 10, mandate: 25, timeskip: 40, failure: 55, log: 78, outcome: 100,
    }
    return map[phase]
  }, [phase])

  const live = journey?.your_config || journey?.full_railwise
  const baseline = journey?.baseline
  const log = side === 'railwise' ? (journey?.railwise_log || []) : (journey?.baseline_log || [])
  const activeCommit = log[commitIdx]
  const currentStep = stepIndex(phase)

  const eventErr = (journey?.event?.error as { code?: string; iso_code?: string } | undefined) || {}
  const iso = (journey?.event as { decline_iso_code?: string } | undefined)?.decline_iso_code || eventErr.iso_code

  const liftPp = journey?.batch_metrics?.lift?.soft_recovery_rate_delta != null
    ? (journey.batch_metrics.lift.soft_recovery_rate_delta * 100)
    : null

  const verdict = useMemo(() => {
    if (!live || !baseline) return null
    if (live.action !== baseline.action) {
      return `Railwise chose ${live.action} while baseline chose ${baseline.action}.`
    }
    return `Both chose ${live.action}.`
  }, [live, baseline])

  return (
    <div className="journey">
      <div className="journey-progress">
        <div className="journey-progress-bar" style={{ width: `${progress}%` }} />
      </div>

      <nav className="story-steps" aria-label="Recovery journey steps">
        {STEPS.map((s, i) => {
          const state = i < currentStep ? 'done' : i === currentStep ? 'now' : 'todo'
          return (
            <div key={s.id} className={`story-step ${state}`}>
              <span className="story-step-num">{i + 1}</span>
              <span className="story-step-label">{s.label}</span>
            </div>
          )
        })}
      </nav>

      {error && <div className="alert alert-error" style={{ margin: '0 0 16px' }}>{error}</div>}

      {/* CHECKOUT */}
      {phase === 'checkout' && (
        <div className="journey-stage">
          <div className="story-guide">
            <span>Step 1 of 6</span>
            <strong>Customer subscribes.</strong>
            <p>UPI AutoPay or a tokenised card, with customer consent.</p>
          </div>
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
                <p className="product-eyebrow">Monthly subscription</p>
                <h2>{PRODUCT.name}</h2>
                <p className="product-tag">{PRODUCT.tagline}</p>
                <ul className="product-features">
                  {PRODUCT.features.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </div>
              <div className="checkout-pay">
                <div className="price-line"><span>Due today</span><strong>₹{PRODUCT.priceInr}</strong></div>
                <div className="price-line muted-line"><span>Then every billing cycle</span><span>₹{PRODUCT.priceInr} / month</span></div>
                <p className="pay-section-label">Choose payment method</p>
                <div className="method-picker">
                  <button type="button" className={`method-card ${payMethod === 'upi' ? 'selected' : ''}`} onClick={() => { setPayMethod('upi'); setScenario('nsf_early_retry') }}>
                    <strong>UPI AutoPay</strong>
                    <span>One-time mandate · recommended</span>
                    <em>arjun@oksbi</em>
                  </button>
                  <button type="button" className={`method-card ${payMethod === 'card' ? 'selected' : ''}`} onClick={() => { setPayMethod('card'); setScenario('nsf_early_retry') }}>
                    <strong>Card</strong>
                    <span>Tokenised recurring (CoFT)</span>
                    <em>•••• 4242 · SBI</em>
                  </button>
                </div>
                {payMethod === 'card' && (
                  <div className="scenario-row">
                    <label>
                      Failure path for this journey
                      <select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
                        <option value="nsf_early_retry">Insufficient funds (ISO 51)</option>
                        <option value="ambiguous_sbi">Ambiguous do_not_honor (ISO 05 · ML)</option>
                        <option value="token_expired">Token expired (CoFT)</option>
                      </select>
                    </label>
                  </div>
                )}
                <label className={`autopay-toggle ${consent ? 'on' : ''}`}>
                  <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                  <span className="autopay-box" />
                  <span>
                    I authorise <strong>{PRODUCT.merchant}</strong> to charge ₹{PRODUCT.priceInr} monthly via{' '}
                    {payMethod === 'upi' ? 'UPI AutoPay' : 'card token'}
                  </span>
                </label>
                <button className="btn-approve" disabled={!consent} onClick={() => setPhase('mandate')}>
                  {payMethod === 'upi' ? 'Approve AutoPay & subscribe' : 'Save card & subscribe'}
                </button>
                <p className="checkout-fine">RBI e-mandate · 24h pre-debit notification · cancel anytime</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* MANDATE */}
      {phase === 'mandate' && (
        <div className="journey-stage center-stage">
          <div className="story-guide">
            <span>Step 2 of 6</span>
            <strong>Mandate is live.</strong>
            <p>The instrument is authorised. Collection waits for the next billing day.</p>
          </div>
          <div className="mandate-cert reveal">
            <div className="mandate-cert-top">
              <span className="rzp-mark">Razorpay</span>
              <span className="mandate-status">ACTIVE</span>
            </div>
            <h2>{payMethod === 'upi' ? 'UPI AutoPay mandate' : 'Card recurring token'}</h2>
            <p className="mandate-merchant">{PRODUCT.merchant} · {PRODUCT.name}</p>
            <div className="mandate-cert-grid">
              <div><span>Mandate / Token</span><strong>{payMethod === 'upi' ? 'mandate_upi_sbi_4419' : 'token_hdfc_coft_991'}</strong></div>
              <div><span>Customer</span><strong>cust_forgecli_arjun</strong></div>
              <div><span>Instrument</span><strong>{payMethod === 'upi' ? 'arjun@oksbi · SBI' : 'Visa •••• 4242 · SBI'}</strong></div>
              <div><span>Max amount</span><strong>₹{PRODUCT.priceInr} / cycle</strong></div>
              <div><span>Frequency</span><strong>As presented · monthly</strong></div>
              <div><span>Next presentation</span><strong>Billing day + 30</strong></div>
            </div>
            <div className="mandate-cert-foot">
              <span>Compliant with RBI e-mandate framework</span>
              <button className="btn-primary" onClick={() => setPhase('timeskip')}>Continue to billing cycle</button>
            </div>
          </div>
        </div>
      )}

      {/* TIME SKIP */}
      {phase === 'timeskip' && (
        <div className="journey-stage center-stage time-skip-stage">
          <div className="story-guide">
            <span>Step 3 of 6</span>
            <strong>One billing cycle later.</strong>
            <p>AutoPay presents the scheduled debit.</p>
          </div>
          <HackerTypeReveal onDone={openFailure} />
        </div>
      )}

      {/* FAILURE */}
      {phase === 'failure' && (
        <div className="journey-stage">
          <div className="story-guide">
            <span>Step 4 of 6</span>
            <strong>The debit failed.</strong>
            <p>This is the payment object the engine receives.</p>
          </div>
          <div className="fail-layout">
            {journey ? (
              <PaymentFailureCard
                title={PRODUCT.name}
                amountPaise={Number(journey.event.amount) || 99900}
                rail={String(journey.event.method || payMethod)}
                issuer={String(journey.event.issuer_bank || 'sbi')}
                decline={eventErr.code || 'unknown'}
                iso={iso}
                paymentId={String(journey.event.id)}
                attempt={String(journey.event.attempt_number ?? 2)}
                status="FAILED"
                customerId={String(journey.event.customer_id || '')}
                createdAt={String(journey.event.created_at || '')}
                mandateId={String(journey.event.mandate_id || journey.event.token_id || '')}
              />
            ) : (
              <div className="card">{loading ? 'Running live engine…' : 'Waiting for engine…'}</div>
            )}

            <div className="fail-side">
              <h3>Live failure feed</h3>
              <p className="muted">Other failures from the same batch</p>
              <div className="feed-list">
                {(journey?.failure_feed || []).slice(0, 5).map((a) => (
                  <div key={a.decision_id} className="rzp-mini-card">
                    <div className="rzp-mini-top">
                      <span className="rzp-mini-fail">FAILED</span>
                      <span className={actionClass(a.action)}>{a.action}</span>
                    </div>
                    <div className="rzp-mini-amt">{inr(a.amount_paise)}</div>
                    <div className="rzp-mini-id">{a.payment_id}</div>
                    <div className="rzp-mini-meta">
                      {(a.issuer_bank || '—').toUpperCase()} · {a.rail.toUpperCase()} · {a.decline_code}
                      {a.decline_iso_code ? ` · ISO ${a.decline_iso_code}` : ''}
                    </div>
                  </div>
                ))}
                {!journey?.failure_feed?.length && !loading && (
                  <p className="muted">No feed yet. The engine warms a batch on first run.</p>
                )}
              </div>
              <div className="fail-live-actions" style={{ marginTop: 14 }}>
                <div className="mini-decision">
                  <span>Baseline</span>
                  <strong className={actionClass(baseline?.action || '')}>{baseline?.action || '—'}</strong>
                </div>
                <div className="mini-decision accent">
                  <span>Railwise</span>
                  <strong className={actionClass(live?.action || '')}>{live?.action || '—'}</strong>
                </div>
              </div>
              <button className="btn-primary" style={{ width: '100%', marginTop: 12 }} disabled={!journey || loading} onClick={openLog}>
                Open decision log →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* DECISION LOG */}
      {phase === 'log' && journey && (
        <div className="journey-stage">
          <div className="story-guide">
            <span>Step 5 of 6</span>
            <strong>Decision log</strong>
            <p>Each row is one choice. Open a hash for the rule, guideline, or model features behind it.</p>
          </div>

          <div className="log-toolbar">
            <div className="ws-toolbar-actions">
              <button className={`seg ${side === 'railwise' ? 'on' : ''}`} onClick={() => { setSide('railwise'); setCommitIdx(0) }}>Railwise</button>
              <button className={`seg ${side === 'baseline' ? 'on' : ''}`} onClick={() => { setSide('baseline'); setCommitIdx(0) }}>Baseline</button>
            </div>
            <div className="ablation-chips">
              {TOGGLE_META.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`chip-toggle ${toggles[t.key] ? 'on' : ''}`}
                  onClick={() => setToggles((prev) => ({ ...prev, [t.key]: !prev[t.key] }))}
                  title={t.hint}
                >
                  {t.label}
                </button>
              ))}
              {loading && <span className="muted">Re-running…</span>}
            </div>
          </div>

          {(journey.action_changed || journey.constraints_changed) && side === 'railwise' && (
            <div className="ablation-flag" style={{ marginBottom: 12 }}>
              Ablation changed this live pass
              {journey.action_changed ? ` · ${journey.full_railwise.action} → ${live?.action}` : ''}
              {journey.constraints_changed ? ' · constraint set changed' : ''}
            </div>
          )}

          <div className="log-layout">
            <div className="commit-list card">
              <div className="commit-list-head">
                <h3>{side === 'railwise' ? 'railwise/decision' : 'baseline/decision'}</h3>
                <span className="muted">{log.length} commits · live</span>
              </div>
              {log.map((c, i) => (
                <CommitRow key={c.id} commit={c} index={i} active={i === commitIdx} onSelect={() => setCommitIdx(i)} />
              ))}
              <div className="ws-nav" style={{ marginTop: 12 }}>
                <button className="btn-ghost-dark" disabled={commitIdx === 0} onClick={() => setCommitIdx((i) => i - 1)}>Previous</button>
                <button
                  className="btn-primary"
                  onClick={() => {
                    if (commitIdx < log.length - 1) setCommitIdx((i) => i + 1)
                    else setPhase('outcome')
                  }}
                >
                  {commitIdx < log.length - 1 ? 'Next commit' : 'See outcome'}
                </button>
              </div>
            </div>

            <div className="commit-detail card">
              {activeCommit ? (
                <>
                  <div className="commit-detail-head">
                    <code>commit {activeCommit.id}</code>
                    <span className={`commit-kind ${activeCommit.ai_used ? 'ai' : 'rules'}`}>
                      {activeCommit.ai_used ? 'AI used' : 'Rules only'}
                    </span>
                  </div>
                  <h2>{activeCommit.title}</h2>
                  <p className="commit-detail-body">{activeCommit.body}</p>

                  {activeCommit.guideline && (
                    <div className="guideline-box">
                      <span>{activeCommit.ai_used ? 'Model scope' : 'Guideline / compliance'}</span>
                      <p>{activeCommit.guideline}</p>
                    </div>
                  )}

                  {activeCommit.forced_action && (
                    <p className="forced-line">
                      Forced action: <span className={actionClass(activeCommit.forced_action)}>{activeCommit.forced_action}</span>
                    </p>
                  )}

                  {activeCommit.ai_used && activeCommit.feature_importance && Object.keys(activeCommit.feature_importance).length > 0 && (
                    <div style={{ marginTop: 14 }}>
                      <h4 style={{ fontSize: '0.85rem', marginBottom: 8 }}>Why the model leaned this way</h4>
                      <div className="weight-list">
                        {Object.entries(activeCommit.feature_importance)
                          .sort((a, b) => b[1] - a[1])
                          .slice(0, 5)
                          .map(([k, v]) => (
                            <div key={k} className="weight-row">
                              <span>{k}</span>
                              <span className="weight-val soft">{(v * 100).toFixed(1)}%</span>
                            </div>
                          ))}
                      </div>
                    </div>
                  )}

                  {activeCommit.reason_chain && activeCommit.reason_chain.length > 0 && (
                    <div style={{ marginTop: 14 }}>
                      <h4 style={{ fontSize: '0.85rem', marginBottom: 8 }}>Reason chain</h4>
                      <ol className="reason-list">
                        {activeCommit.reason_chain.map((r, i) => <li key={i}>{r}</li>)}
                      </ol>
                    </div>
                  )}

                  {side === 'railwise' && (
                    <div className="compare-strip">
                      <div>
                        <span>Railwise</span>
                        <strong className={actionClass(live?.action || '')}>{live?.action}</strong>
                      </div>
                      <div>
                        <span>Baseline</span>
                        <strong className={actionClass(baseline?.action || '')}>{baseline?.action}</strong>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p className="muted">Select a commit</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* OUTCOME */}
      {phase === 'outcome' && journey && (
        <div className="journey-stage">
          <div className="story-guide">
            <span>Step 6 of 6</span>
            <strong>Same mandate. Clearer judgment.</strong>
            <p>{verdict}</p>
          </div>
          <div className="outcome-grid">
            <div className="outcome-card bad">
              <h3>Baseline</h3>
              <div className="outcome-stat">{inr(baseline?.recovered_amount_paise || 0)}</div>
              <ul>
                <li>Action: <span className={actionClass(baseline?.action || '')}>{baseline?.action}</span></li>
                <li>Result: {baseline?.execution_result}</li>
                <li>Static retry schedule</li>
              </ul>
            </div>
            <div className="outcome-card good">
              <h3>Railwise</h3>
              <div className="outcome-stat">{inr(live?.recovered_amount_paise || 0)}</div>
              <ul>
                <li>Action: <span className={actionClass(live?.action || '')}>{live?.action}</span></li>
                <li>Result: {live?.execution_result}</li>
                <li>Constraints: {(live?.constraint_hits || []).map((h) => h.code).join(', ') || 'none'}</li>
              </ul>
            </div>
          </div>
          {journey.batch_metrics?.railwise && journey.batch_metrics?.baseline && (
            <div className="batch-proof card">
              <h3>Batch proof (live)</h3>
              <p className="muted" style={{ marginTop: -6, marginBottom: 12 }}>
                Soft recovery lift vs baseline
                {liftPp != null ? `: ${liftPp >= 0 ? '+' : ''}${liftPp.toFixed(1)} pp` : ''}
              </p>
              <div className="kpi-grid" style={{ marginBottom: 0 }}>
                <div className="kpi-card kpi-good">
                  <div className="kpi-label">Soft recovery · Railwise</div>
                  <div className="kpi-value">{(journey.batch_metrics.railwise.soft_recovery_rate * 100).toFixed(1)}%</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">Soft recovery · Baseline</div>
                  <div className="kpi-value">{(journey.batch_metrics.baseline.soft_recovery_rate * 100).toFixed(1)}%</div>
                </div>
                <div className="kpi-card kpi-good">
                  <div className="kpi-label">Hard wasted</div>
                  <div className="kpi-value">{journey.batch_metrics.railwise.hard_decline_wasted_retries}</div>
                </div>
                <div className="kpi-card kpi-good">
                  <div className="kpi-label">UPI violations</div>
                  <div className="kpi-value">{journey.batch_metrics.railwise.upi_cooldown_violations}</div>
                </div>
              </div>
            </div>
          )}
          <div className="fork-cta">
            <button className="btn-secondary" onClick={reset}>Replay from checkout</button>
            <button className="btn-primary" onClick={() => setPhase('log')}>Back to decision log</button>
          </div>
        </div>
      )}
    </div>
  )
}
