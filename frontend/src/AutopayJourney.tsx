import { useCallback, useEffect, useMemo, useState } from 'react'

const API = '/api'

type Phase = 'checkout' | 'mandate' | 'timeskip' | 'failure' | 'workspace' | 'outcome'

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
  policy_name?: string
}

type Stage = {
  id: string
  title: string
  kind: string
  summary: string
  fields: Record<string, unknown>
}

type JourneyResult = {
  event: Record<string, unknown>
  baseline: Decision
  full_railwise: Decision
  your_config: Decision
  action_changed: boolean
  constraints_changed?: boolean
  diff_summary?: {
    full_action: string
    custom_action: string
    full_constraints: string[]
    custom_constraints: string[]
    full_delay_minutes?: number | null
    custom_delay_minutes?: number | null
  }
  stages: Stage[]
  failure_feed: Array<{
    decision_id: string
    payment_id: string
    rail: string
    issuer_bank?: string
    decline_code: string
    amount_paise: number
    action: string
    recoverability: number
    decline_kind: string
    classification_source?: string
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

const DEFAULT_TOGGLES: Toggles = {
  use_ml_model: true,
  use_compliance_blocks: true,
  use_issuer_health: true,
  use_mandate_vitality: true,
  use_timing_ai: true,
}

const TOGGLE_META: Array<{ key: keyof Toggles; label: string; impact: string }> = [
  { key: 'use_ml_model', label: 'ML classifier', impact: 'Ambiguous codes (ISO 05) use logistic model' },
  { key: 'use_compliance_blocks', label: 'Compliance gate', impact: 'NPCI gap, PDN, attempt budget, hard declines' },
  { key: 'use_issuer_health', label: 'Issuer health', impact: 'Cross-customer SBI/Bandhan backoff' },
  { key: 'use_mandate_vitality', label: 'Mandate vitality', impact: 'Proactive dunning on dying mandates' },
  { key: 'use_timing_ai', label: 'Timing policy', impact: 'Payday / non-peak slot ranking' },
]

function inr(paise: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
}

function actionClass(action: string) {
  return `action-badge action-${action}`
}

/** Hacker-style letter reveal for "30 DAYS LATER" */
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
          {done && (
            <p className="hacker-sub reveal">Same calendar day · AutoPay presentation queued</p>
          )}
        </div>
      </div>
      {done && (
        <button className="btn-primary" onClick={onDone}>Open debit attempt</button>
      )}
    </div>
  )
}

export default function AutopayJourney() {
  const [phase, setPhase] = useState<Phase>('checkout')
  const [payMethod, setPayMethod] = useState<PayMethod>('upi')
  const [scenario, setScenario] = useState<Scenario>('nsf_early_retry')
  const [consent, setConsent] = useState(false)
  const [toggles, setToggles] = useState<Toggles>(DEFAULT_TOGGLES)
  const [journey, setJourney] = useState<JourneyResult | null>(null)
  const [activeStage, setActiveStage] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showBaseline, setShowBaseline] = useState(true)

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
      setError(e instanceof Error ? e.message : 'Engine unavailable — start backend on :8000')
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  // Live ablation: re-run engine whenever toggles change in workspace/outcome
  useEffect(() => {
    if (phase === 'workspace' || phase === 'outcome' || phase === 'failure') {
      runJourney(payMethod, scenario, toggles)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toggles])

  function reset() {
    setPhase('checkout')
    setConsent(false)
    setJourney(null)
    setActiveStage(0)
    setToggles(DEFAULT_TOGGLES)
    setError(null)
  }

  async function approveMandate() {
    if (!consent) return
    setPhase('mandate')
  }

  async function openFailure() {
    setPhase('failure')
    await runJourney(payMethod, scenario, toggles)
  }

  async function enterWorkspace() {
    setPhase('workspace')
    setActiveStage(0)
    if (!journey) await runJourney(payMethod, scenario, toggles)
  }

  const progress = useMemo(() => {
    const map: Record<Phase, number> = {
      checkout: 8, mandate: 22, timeskip: 36, failure: 50, workspace: 72, outcome: 100,
    }
    return map[phase]
  }, [phase])

  const live = journey?.your_config || journey?.full_railwise
  const full = journey?.full_railwise
  const baseline = journey?.baseline
  const stage = journey?.stages?.[activeStage]

  return (
    <div className="journey">
      <div className="journey-progress">
        <div className="journey-progress-bar" style={{ width: `${progress}%` }} />
      </div>
      {error && <div className="alert alert-error" style={{ margin: '0 0 16px' }}>{error}</div>}

      {/* ── CHECKOUT ── */}
      {phase === 'checkout' && (
        <div className="journey-stage">
          <div className="checkout-shell">
            <div className="checkout-brand-bar">
              <span className="checkout-logo">ForgeCLI</span>
              <span className="checkout-secure">Secured by Razorpay · Test mode</span>
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
                <div className="price-line">
                  <span>Due today</span>
                  <strong>₹{PRODUCT.priceInr}</strong>
                </div>
                <div className="price-line muted-line">
                  <span>Then every billing cycle</span>
                  <span>₹{PRODUCT.priceInr} / month</span>
                </div>

                <p className="pay-section-label">Choose payment method</p>
                <div className="method-picker">
                  <button
                    type="button"
                    className={`method-card ${payMethod === 'upi' ? 'selected' : ''}`}
                    onClick={() => { setPayMethod('upi'); setScenario('nsf_early_retry') }}
                  >
                    <strong>UPI AutoPay</strong>
                    <span>One-time mandate · recommended</span>
                    <em>arjun@oksbi</em>
                  </button>
                  <button
                    type="button"
                    className={`method-card ${payMethod === 'card' ? 'selected' : ''}`}
                    onClick={() => { setPayMethod('card'); setScenario('nsf_early_retry') }}
                  >
                    <strong>Card</strong>
                    <span>Tokenised recurring (CoFT)</span>
                    <em>•••• 4242 · SBI</em>
                  </button>
                </div>

                {payMethod === 'card' && (
                  <div className="scenario-row">
                    <label>
                      Failure scenario for this journey
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

                <button className="btn-approve" disabled={!consent} onClick={approveMandate}>
                  {payMethod === 'upi' ? 'Approve AutoPay & subscribe' : 'Save card & subscribe'}
                </button>
                <p className="checkout-fine">RBI e-mandate · 24h pre-debit notification · cancel anytime</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── MANDATE ── */}
      {phase === 'mandate' && (
        <div className="journey-stage center-stage">
          <div className="mandate-cert reveal">
            <div className="mandate-cert-top">
              <span className="rzp-mark">Razorpay</span>
              <span className="mandate-status">ACTIVE</span>
            </div>
            <h2>{payMethod === 'upi' ? 'UPI AutoPay mandate' : 'Card recurring token'}</h2>
            <p className="mandate-merchant">{PRODUCT.merchant} · {PRODUCT.name}</p>
            <div className="mandate-cert-grid">
              <div>
                <span>Mandate / Token</span>
                <strong>{payMethod === 'upi' ? 'mandate_upi_sbi_4419' : 'token_hdfc_coft_991'}</strong>
              </div>
              <div>
                <span>Customer</span>
                <strong>cust_forgecli_arjun</strong>
              </div>
              <div>
                <span>Instrument</span>
                <strong>{payMethod === 'upi' ? 'arjun@oksbi · SBI' : 'Visa •••• 4242 · SBI'}</strong>
              </div>
              <div>
                <span>Max amount</span>
                <strong>₹{PRODUCT.priceInr} / cycle</strong>
              </div>
              <div>
                <span>Frequency</span>
                <strong>As presented · monthly</strong>
              </div>
              <div>
                <span>Next presentation</span>
                <strong>Billing day + 30</strong>
              </div>
            </div>
            <div className="mandate-cert-foot">
              <span>Compliant with RBI e-mandate framework</span>
              <button className="btn-primary" onClick={() => setPhase('timeskip')}>Continue to billing cycle</button>
            </div>
          </div>
        </div>
      )}

      {/* ── TIME SKIP ── */}
      {phase === 'timeskip' && (
        <div className="journey-stage center-stage time-skip-stage">
          <HackerTypeReveal onDone={openFailure} />
        </div>
      )}

      {/* ── FAILURE ── */}
      {phase === 'failure' && (
        <div className="journey-stage">
          <div className="fail-layout">
            <div className="fail-card-pro reveal">
              <div className="fail-banner">AUTOPAY DEBIT DECLINED</div>
              <h2>{PRODUCT.name}</h2>
              <p className="fail-amount">{inr(99900)}</p>
              {loading && <p className="muted">Running live engine pass…</p>}
              {journey && (
                <>
                  <div className="fail-meta">
                    <div><span>Payment</span><strong>{String(journey.event.id)}</strong></div>
                    <div><span>Rail</span><strong>{String(journey.event.method).toUpperCase()}</strong></div>
                    <div><span>Issuer</span><strong>{String(journey.event.issuer_bank).toUpperCase()}</strong></div>
                    <div>
                      <span>Decline</span>
                      <strong>
                        {(journey.event.error as { code?: string })?.code}
                        {(journey.event as { decline_iso_code?: string }).decline_iso_code
                          ? ` · ISO ${(journey.event as { decline_iso_code?: string }).decline_iso_code}`
                          : ''}
                      </strong>
                    </div>
                    <div><span>Attempt</span><strong>#{String(journey.event.attempt_number)}</strong></div>
                    <div><span>Hours since last</span><strong>{String(journey.event.hours_since_last_attempt)}</strong></div>
                  </div>
                  <div className="fail-live-actions">
                    <div className="mini-decision">
                      <span>Baseline would</span>
                      <strong className={actionClass(baseline?.action || '')}>{baseline?.action}</strong>
                    </div>
                    <div className="mini-decision accent">
                      <span>Railwise decides</span>
                      <strong className={actionClass(full?.action || '')}>{full?.action}</strong>
                    </div>
                  </div>
                </>
              )}
              <button className="btn-primary" disabled={loading || !journey} onClick={enterWorkspace}>
                Open recovery workspace
              </button>
            </div>

            <div className="fail-side">
              <h3>Live failure feed</h3>
              <p className="muted">Same shapes from the current batch — delayed retries &amp; audits</p>
              <div className="feed-list">
                {(journey?.failure_feed || []).map((a) => (
                  <div key={a.decision_id} className="feed-item">
                    <div className="feed-top">
                      <span className="fc-rail">{a.rail.toUpperCase()}</span>
                      <span className={actionClass(a.action)}>{a.action}</span>
                    </div>
                    <div className="feed-mid">
                      {a.issuer_bank?.toUpperCase()} · {a.decline_code}
                    </div>
                    <div className="feed-bot">
                      {inr(a.amount_paise)} · {a.decline_kind} · recov {(a.recoverability * 100).toFixed(0)}%
                    </div>
                  </div>
                ))}
                {!journey?.failure_feed?.length && !loading && (
                  <p className="muted">Feed loads with the engine pass.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── WORKSPACE (main interactive storytelling) ── */}
      {phase === 'workspace' && journey && (
        <div className="journey-stage workspace">
          <div className="ws-toolbar">
            <div>
              <h2>Recovery workspace</h2>
              <p className="muted">Live engine output · toggle layers to measure impact</p>
            </div>
            <div className="ws-toolbar-actions">
              <button className={`seg ${showBaseline ? 'on' : ''}`} onClick={() => setShowBaseline(true)}>Baseline</button>
              <button className={`seg ${!showBaseline ? 'on' : ''}`} onClick={() => setShowBaseline(false)}>Railwise</button>
              <button className="btn-primary" onClick={() => setPhase('outcome')}>View outcomes</button>
            </div>
          </div>

          <div className="ws-grid">
            {/* Pipeline */}
            <aside className="ws-pipeline">
              <h3>Pipeline</h3>
              {journey.stages.map((s, i) => (
                <button
                  key={s.id}
                  className={`pipe-step ${i === activeStage ? 'active' : ''} ${i < activeStage ? 'done' : ''}`}
                  onClick={() => setActiveStage(i)}
                >
                  <span className="pipe-idx">{i + 1}</span>
                  <div>
                    <strong>{s.title}</strong>
                    <em>{s.kind === 'ai' ? 'AI' : s.kind.toUpperCase()}</em>
                  </div>
                </button>
              ))}
            </aside>

            {/* Stage detail */}
            <section className="ws-main">
              {stage && (
                <>
                  <div className="ws-stage-head">
                    <h3>{stage.title}</h3>
                    <p>{stage.summary}</p>
                  </div>

                  <div className="ws-compare">
                    <div className={`ws-pane ${showBaseline ? 'focus' : ''}`}>
                      <div className="pane-label">Baseline · static hourly</div>
                      <div className={`path-card tone-warn`}>
                        <span className="path-badge">BASELINE</span>
                        <h3 className={actionClass(baseline?.action || '')}>{baseline?.action}</h3>
                        <p>
                          Recoverability ignored beyond binary retryability.
                          {baseline?.delay_minutes != null ? ` Delay ${baseline.delay_minutes}m.` : ''}
                        </p>
                        <div className="path-metrics">
                          <div className="path-metric"><span>Kind</span><strong>{baseline?.classification.decline_kind}</strong></div>
                          <div className="path-metric"><span>Source</span><strong>template</strong></div>
                          <div className="path-metric"><span>Result</span><strong>{baseline?.execution_result || '—'}</strong></div>
                          <div className="path-metric"><span>Recovered</span><strong>{inr(baseline?.recovered_amount_paise || 0)}</strong></div>
                        </div>
                      </div>
                    </div>

                    <div className={`ws-pane ${!showBaseline ? 'focus' : ''}`}>
                      <div className="pane-label accent">Railwise · your config</div>
                      <div className={`path-card ${journey.action_changed ? 'tone-warn' : 'tone-good'}`}>
                        <span className="path-badge">{live?.classification.source === 'model' ? 'ML' : 'LIVE'}</span>
                        <h3 className={actionClass(live?.action || '')}>{live?.action}</h3>
                        <p>
                          {live?.classification.decline_kind} · recov {live?.classification.recoverability.toFixed(2)} ·{' '}
                          {live?.classification.source}
                          {live?.delay_minutes != null ? ` · delay ${Math.round(live.delay_minutes)}m` : ''}
                        </p>
                        {!!live?.constraint_hits?.length && (
                          <div className="constraint-tags" style={{ marginBottom: 10 }}>
                            {live.constraint_hits.map((h) => (
                              <span key={h.code} className="constraint-tag" title={h.message}>{h.code}</span>
                            ))}
                          </div>
                        )}
                        <div className="path-metrics">
                          <div className="path-metric"><span>Issuer health</span><strong>{live?.issuer_health_level || '—'}</strong></div>
                          <div className="path-metric"><span>Vitality</span><strong>{live?.mandate_vitality_level || '—'}</strong></div>
                          <div className="path-metric"><span>Result</span><strong>{live?.execution_result || '—'}</strong></div>
                          <div className="path-metric"><span>Recovered</span><strong>{inr(live?.recovered_amount_paise || 0)}</strong></div>
                        </div>
                        {journey.action_changed && (
                          <p className="ablation-flag">Ablation changed action vs full Railwise ({full?.action})</p>
                        )}
                        {!journey.action_changed && journey.constraints_changed && (
                          <p className="ablation-flag">
                            Constraints changed: [{(journey.diff_summary?.full_constraints || []).join(', ')}] → [{(journey.diff_summary?.custom_constraints || []).join(', ')}]
                          </p>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Stage-specific deep cards */}
                  {stage.id === 'classify' && (
                    <div className="ws-deep card">
                      <h4>Classification detail</h4>
                      <p className="muted">
                        {live?.classification.source === 'model'
                          ? 'Ambiguous code — logistic SGD voted. Feature importance below.'
                          : 'Clear ISO/semantic code — rules only. ML did not vote.'}
                      </p>
                      {live?.classification.feature_importance && Object.keys(live.classification.feature_importance).length > 0 ? (
                        <div className="weight-list">
                          {Object.entries(live.classification.feature_importance)
                            .sort((a, b) => b[1] - a[1])
                            .slice(0, 6)
                            .map(([k, v]) => (
                              <div key={k} className="weight-row">
                                <span>{k}</span>
                                <span className="weight-val soft">{(v * 100).toFixed(1)}%</span>
                              </div>
                            ))}
                        </div>
                      ) : (
                        <div className="reason-chips">
                          {(live?.classification.reason_codes || []).map((r) => (
                            <span key={r} className="constraint-tag">{r}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {stage.id === 'constraints' && (
                    <div className="ws-deep card">
                      <h4>Constraint hits (live)</h4>
                      {(live?.constraint_hits || []).length === 0 && <p className="muted">No forced constraints — policy free within legal set.</p>}
                      {(live?.constraint_hits || []).map((h) => (
                        <div key={h.code} className="constraint-block">
                          <strong>{h.code}</strong>
                          <p>{h.message}</p>
                          {h.forced_action && <span className={actionClass(h.forced_action)}>{h.forced_action}</span>}
                        </div>
                      ))}
                    </div>
                  )}

                  {stage.id === 'policy' && (
                    <div className="ws-deep card">
                      <h4>Reason chain</h4>
                      <ol className="reason-list">
                        {(live?.reason_chain || []).map((r, i) => <li key={i}>{r}</li>)}
                      </ol>
                    </div>
                  )}

                  <div className="ws-nav">
                    <button className="btn-ghost-dark" disabled={activeStage === 0} onClick={() => setActiveStage((s) => s - 1)}>
                      Previous stage
                    </button>
                    <button
                      className="btn-primary"
                      onClick={() => {
                        if (activeStage < journey.stages.length - 1) setActiveStage((s) => s + 1)
                        else setPhase('outcome')
                      }}
                    >
                      {activeStage < journey.stages.length - 1 ? 'Next stage' : 'See final outcomes'}
                    </button>
                  </div>
                </>
              )}
            </section>

            {/* Ablation panel */}
            <aside className="ws-ablation">
              <h3>Live ablation</h3>
              <p className="muted">Toggle a layer — engine re-runs instantly</p>
              <div className="toggle-grid">
                {TOGGLE_META.map((t) => (
                  <label key={t.key} className="toggle-item">
                    <div>
                      <strong>{t.label}</strong>
                      <span>{t.impact}</span>
                    </div>
                    <button
                      type="button"
                      className={`toggle-switch ${toggles[t.key] ? 'on' : 'off'}`}
                      onClick={() => setToggles((prev) => ({ ...prev, [t.key]: !prev[t.key] }))}
                    >
                      <span className="toggle-knob" />
                    </button>
                  </label>
                ))}
              </div>
              {loading && <p className="muted">Updating decision…</p>}
              <div className="ablation-summary">
                <div>
                  <span>Full Railwise</span>
                  <strong className={actionClass(full?.action || '')}>{full?.action}</strong>
                </div>
                <div>
                  <span>Your config</span>
                  <strong className={actionClass(live?.action || '')}>{live?.action}</strong>
                </div>
                {journey.diff_summary && (
                  <div>
                    <span>Constraints</span>
                    <strong style={{ fontSize: '0.7rem' }}>
                      {(journey.diff_summary.custom_constraints || []).join(', ') || 'none'}
                    </strong>
                  </div>
                )}
              </div>
              <button className="btn-secondary" onClick={() => runJourney(payMethod, scenario, toggles)} disabled={loading}>
                Re-run engine pass
              </button>
            </aside>
          </div>
        </div>
      )}

      {/* ── OUTCOME ── */}
      {phase === 'outcome' && journey && (
        <div className="journey-stage">
          <div className="outcome-intro reveal">
            <h2>Same mandate. Measured difference.</h2>
            <p>Numbers below are from the live `/journey/run` pass — not scripted copy.</p>
          </div>
          <div className="outcome-grid">
            <div className="outcome-card bad">
              <h3>Baseline</h3>
              <div className="outcome-stat">{inr(baseline?.recovered_amount_paise || 0)}</div>
              <ul>
                <li>Action: {baseline?.action}</li>
                <li>Result: {baseline?.execution_result}</li>
                <li>No constraint audit trail</li>
                <li>Static timing template</li>
              </ul>
            </div>
            <div className="outcome-card good">
              <h3>Railwise (your config)</h3>
              <div className="outcome-stat">{inr(live?.recovered_amount_paise || 0)}</div>
              <ul>
                <li>Action: {live?.action}</li>
                <li>Result: {live?.execution_result}</li>
                <li>Constraints: {(live?.constraint_hits || []).map((h) => h.code).join(', ') || 'none'}</li>
                <li>Source: {live?.classification.source}</li>
              </ul>
            </div>
          </div>

          {journey.batch_metrics?.railwise && journey.batch_metrics?.baseline && (
            <div className="batch-proof card">
              <h3>Batch context (same engine)</h3>
              <div className="kpi-grid" style={{ marginBottom: 0 }}>
                <div className="kpi-card kpi-good">
                  <div className="kpi-label">Soft recovery Railwise</div>
                  <div className="kpi-value">{(journey.batch_metrics.railwise.soft_recovery_rate * 100).toFixed(1)}%</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">Soft recovery Baseline</div>
                  <div className="kpi-value">{(journey.batch_metrics.baseline.soft_recovery_rate * 100).toFixed(1)}%</div>
                </div>
                <div className="kpi-card kpi-good">
                  <div className="kpi-label">Hard wasted (Railwise)</div>
                  <div className="kpi-value">{journey.batch_metrics.railwise.hard_decline_wasted_retries}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">UPI violations (Railwise)</div>
                  <div className="kpi-value">{journey.batch_metrics.railwise.upi_cooldown_violations}</div>
                </div>
              </div>
            </div>
          )}

          <div className="outcome-proof reveal">
            <div className="proof-item">
              <strong>Test it yourself</strong>
              <p>Use Live ablation above (or Sandbox Lab) — turning Compliance off often changes UPI gap decisions immediately.</p>
            </div>
            <div className="proof-item">
              <strong>ML only when needed</strong>
              <p>Pick Card → Ambiguous do_not_honor on checkout to force model classification and feature weights.</p>
            </div>
            <div className="proof-item">
              <strong>Overview feed</strong>
              <p>Delayed retries in Overview share the same audit schema as the live failure feed on the decline page.</p>
            </div>
          </div>

          <div className="fork-cta">
            <button className="btn-secondary" onClick={reset}>Replay from checkout</button>
            <button className="btn-primary" onClick={() => setPhase('workspace')}>Back to workspace</button>
          </div>
        </div>
      )}
    </div>
  )
}
