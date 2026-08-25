import { useEffect, useMemo, useState } from 'react'
import './App.css'

type Metrics = {
  policy_name: string
  total_failures: number
  soft_failures: number
  hard_failures: number
  recovered_count: number
  recovered_paise: number
  soft_recovery_rate: number
  hard_decline_wasted_retries: number
  upi_cooldown_violations: number
  audit_coverage_pct: number
  action_counts: Record<string, number>
}

type Audit = {
  decision_id: string
  payment_id: string
  rail: string
  decline_code: string
  amount_paise: number
  attempt_number: number
  action: string
  decline_kind: string
  recoverability: number
  classification_source: string
  feature_importance?: Record<string, number>
  constraint_hits: Array<{ code: string; message: string }>
  reason_chain: string[]
  policy_name: string
  execution_result?: string
  recovered_amount_paise: number
  delay_minutes?: number
  target_rail?: string | null
}

type BatchResult = {
  batch_id: string
  railwise: Metrics
  baseline: Metrics
  sample_audits: Audit[]
  featured?: Audit | null
  lift: {
    soft_recovery_rate_delta: number
    recovered_paise_delta: number
    hard_wasted_delta: number
    upi_violations_delta: number
  }
}

type EdgeCase = {
  id: string
  title: string
  notes: string
  fixture: {
    method?: string
    error?: { code?: string }
    amount?: number
    attempt_number?: number
  }
  decision: {
    decision_id: string
    payment_id: string
    action: string
    classification: {
      decline_kind: string
      recoverability: number
      source: string
      feature_importance?: Record<string, number>
    }
    constraint_hits: Array<{ code: string; message: string }>
    reason_chain: string[]
    policy_name: string
    execution_result?: string
    recovered_amount_paise: number
    delay_minutes?: number
    target_rail?: string | null
  }
}

const API = '/api'

function inr(paise: number) {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(paise / 100)
}

function pct(n: number) {
  return `${(n * 100).toFixed(1)}%`
}

export default function App() {
  const [batch, setBatch] = useState<BatchResult | null>(null)
  const [edgeCases, setEdgeCases] = useState<EdgeCase[]>([])
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<'audits' | 'edges'>('edges')
  const [selected, setSelected] = useState<Audit | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function loadEdges() {
    const res = await fetch(`${API}/edge-cases`)
    const data = await res.json()
    setEdgeCases(data.edge_cases || [])
    const featured = (data.edge_cases || []).find((e: EdgeCase) => e.id === 'upi_budget_exhausted_rail_switch')
    if (featured) {
      setSelected(edgeDecisionToAudit(featured))
    }
  }

  async function runBatch(n = 500) {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/batch/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ n, seed: 42, persist: true }),
      })
      if (!res.ok) throw new Error(`Batch failed (${res.status})`)
      const data: BatchResult = await res.json()
      setBatch(data)
      setTab('audits')
      if (data.featured) setSelected(data.featured)
      else if (data.sample_audits?.[0]) setSelected(data.sample_audits[0])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to run batch')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadEdges().catch(() => setError('API offline — start backend on :8000'))
    fetch(`${API}/batch/latest`)
      .then((r) => r.json())
      .then((data: BatchResult) => {
        if (data?.railwise) {
          setBatch(data)
          if (data.featured) setSelected(data.featured)
        }
      })
      .catch(() => undefined)
  }, [])

  const reasonSteps = useMemo(() => selected?.reason_chain || [], [selected])

  return (
    <div className="app">
      <header className="hero">
        <p className="pill">
          <span className="dot" />
          Track 03 · AI Revenue Recovery · constraint-first
        </p>
        <h1 className="brand">
          Rail<span>wise</span>
        </h1>
        <p className="tagline">
          A rail-aware recovery decision engine for UPI AutoPay and cards. Compliance ceilings beat
          recoverability scores — every edge case has a tested priority order and an audit trail.
        </p>
        <div className="cta-row">
          <button className="btn btn-primary" disabled={loading} onClick={() => runBatch(500)}>
            {loading ? 'Running batch…' : 'Run 500-failure A/B batch'}
          </button>
          <button className="btn btn-ghost" onClick={() => setTab('edges')}>
            Edge-case gallery
          </button>
        </div>
        {error && <p className="empty">{error}</p>}
      </header>

      {batch && (
        <section className="metrics">
          <div className="metric">
            <div className="label">Soft recovery (Railwise)</div>
            <div className="value">{pct(batch.railwise.soft_recovery_rate)}</div>
            <div className="delta">
              vs baseline {pct(batch.baseline.soft_recovery_rate)} (
              {batch.lift.soft_recovery_rate_delta >= 0 ? '+' : ''}
              {pct(batch.lift.soft_recovery_rate_delta)})
            </div>
          </div>
          <div className="metric">
            <div className="label">₹ recovered lift</div>
            <div className="value">{inr(Math.max(0, batch.lift.recovered_paise_delta))}</div>
            <div className="delta">
              Railwise {inr(batch.railwise.recovered_paise)} · Baseline {inr(batch.baseline.recovered_paise)}
            </div>
          </div>
          <div className="metric">
            <div className="label">Hard-decline wasted retries</div>
            <div className="value">{batch.railwise.hard_decline_wasted_retries}</div>
            <div className="delta">Baseline wasted {batch.baseline.hard_decline_wasted_retries}</div>
          </div>
          <div className={`metric ${batch.railwise.upi_cooldown_violations ? 'warn' : ''}`}>
            <div className="label">UPI cooldown violations</div>
            <div className="value">{batch.railwise.upi_cooldown_violations}</div>
            <div className="delta">Baseline {batch.baseline.upi_cooldown_violations} · audit {batch.railwise.audit_coverage_pct}%</div>
          </div>
        </section>
      )}

      <div className="layout">
        <section className="panel">
          <h2>Decision explorer</h2>
          <p className="sub">Click a failure to see the full reason chain — constraints first, then policy.</p>

          {batch && (
            <div className="compare">
              <div className="compare-col win">
                <h3>Railwise</h3>
                <p>Recovered: {inr(batch.railwise.recovered_paise)}</p>
                <p>Hard waste: {batch.railwise.hard_decline_wasted_retries}</p>
                <p>UPI violations: {batch.railwise.upi_cooldown_violations}</p>
              </div>
              <div className="compare-col">
                <h3>Static baseline</h3>
                <p>Recovered: {inr(batch.baseline.recovered_paise)}</p>
                <p>Hard waste: {batch.baseline.hard_decline_wasted_retries}</p>
                <p>UPI violations: {batch.baseline.upi_cooldown_violations}</p>
              </div>
            </div>
          )}

          <div className="tabs">
            <button className={`tab ${tab === 'edges' ? 'active' : ''}`} onClick={() => setTab('edges')}>
              Edge cases
            </button>
            <button className={`tab ${tab === 'audits' ? 'active' : ''}`} onClick={() => setTab('audits')}>
              Batch audits
            </button>
          </div>

          <div className="list">
            {tab === 'edges' &&
              edgeCases.map((ec) => {
                const audit = edgeDecisionToAudit(ec)
                return (
                  <button
                    key={ec.id}
                    className={`list-item ${selected?.payment_id === audit.payment_id ? 'selected' : ''}`}
                    onClick={() => setSelected(audit)}
                  >
                    <div className="row">
                      <strong>{ec.title}</strong>
                      <span className={`badge ${audit.action}`}>{audit.action}</span>
                    </div>
                    <div className="muted">{ec.notes}</div>
                  </button>
                )
              })}
            {tab === 'audits' &&
              (batch?.sample_audits || []).map((a) => (
                <button
                  key={a.decision_id}
                  className={`list-item ${selected?.decision_id === a.decision_id ? 'selected' : ''}`}
                  onClick={() => setSelected(a)}
                >
                  <div className="row">
                    <strong>
                      {a.rail.toUpperCase()} · {a.decline_code}
                    </strong>
                    <span className={`badge ${a.action}`}>{a.action}</span>
                  </div>
                  <div className="muted">
                    {a.payment_id} · attempt {a.attempt_number} · {inr(a.amount_paise)}
                  </div>
                </button>
              ))}
            {tab === 'audits' && !batch && <div className="empty">Run a batch to load audits.</div>}
          </div>
        </section>

        <section className="panel">
          <h2>Reason chain</h2>
          <p className="sub">Why this action — signals, constraints, and where AI did or did not vote.</p>

          {!selected && <div className="empty">Select a decision to inspect.</div>}

          {selected && (
            <div className="trace">
              <div className="trace-step" style={{ animationDelay: '0ms' }}>
                <strong>Input</strong>
                {selected.rail} · {selected.decline_code} · attempt {selected.attempt_number} ·{' '}
                {inr(selected.amount_paise)} · kind {selected.decline_kind}
              </div>
              <div className="trace-step" style={{ animationDelay: '40ms' }}>
                <strong>Classification</strong>
                recoverability {selected.recoverability?.toFixed?.(2) ?? selected.recoverability} · source{' '}
                {selected.classification_source}
                {selected.feature_importance && Object.keys(selected.feature_importance).length > 0 && (
                  <div className="muted" style={{ marginTop: 6 }}>
                    Top features:{' '}
                    {Object.entries(selected.feature_importance)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 3)
                      .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
                      .join(' · ')}
                  </div>
                )}
              </div>
              {(selected.constraint_hits || []).map((h, i) => (
                <div key={h.code + i} className="trace-step" style={{ animationDelay: `${80 + i * 40}ms` }}>
                  <strong>Constraint · {h.code}</strong>
                  {h.message}
                </div>
              ))}
              {reasonSteps.map((step, i) => (
                <div key={step + i} className="trace-step" style={{ animationDelay: `${120 + i * 30}ms` }}>
                  <strong>Step {i + 1}</strong>
                  {step}
                </div>
              ))}
              <div className="trace-step" style={{ animationDelay: '220ms' }}>
                <strong>Final action</strong>
                <span className={`badge ${selected.action}`}>{selected.action}</span>
                {selected.target_rail ? ` → ${selected.target_rail}` : ''}
                {selected.delay_minutes != null ? ` · delay ${Math.round(selected.delay_minutes)}m` : ''}
                {selected.execution_result ? ` · ${selected.execution_result}` : ''}
              </div>

              {selected.constraint_hits?.some((h) => h.code === 'attempt_budget_exhausted') && (
                <div className="featured">
                  <h3>Featured failure — what broke, how we got out</h3>
                  <p>
                    Classification still scored this debit as recoverable (soft NSF / high
                    recoverability), but the UPI attempt budget was exhausted (1 original + 3 retries).
                    Soft signal lost to the hard ceiling: no further debit — rail-switch / payment link
                    instead. That priority order is the product.
                  </p>
                  <code>compliance ceiling &gt; recoverability score → rail_switch</code>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function edgeDecisionToAudit(ec: EdgeCase): Audit {
  const d = ec.decision
  const fixture = ec.fixture
  return {
    decision_id: d.decision_id,
    payment_id: d.payment_id,
    rail: fixture?.method || 'card',
    decline_code: fixture?.error?.code || 'unknown',
    amount_paise: fixture?.amount || 0,
    attempt_number: fixture?.attempt_number || 1,
    action: d.action,
    decline_kind: d.classification?.decline_kind || 'soft',
    recoverability: d.classification?.recoverability ?? 0,
    classification_source: d.classification?.source || 'rules',
    feature_importance: d.classification?.feature_importance,
    constraint_hits: d.constraint_hits || [],
    reason_chain: d.reason_chain || [],
    policy_name: d.policy_name || 'railwise',
    execution_result: d.execution_result,
    recovered_amount_paise: d.recovered_amount_paise || 0,
    delay_minutes: d.delay_minutes,
    target_rail: d.target_rail,
  }
}
