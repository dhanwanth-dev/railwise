import { useEffect, useState } from 'react'
import './App.css'
import AutopayJourney from './AutopayJourney'

const API = '/api'

// ── Types ────────────────────────────────────────────────────────────────────

type Metrics = {
  soft_recovery_rate: number
  recovered_paise: number
  hard_decline_wasted_retries: number
  upi_cooldown_violations: number
  audit_coverage_pct: number
  pdn_compliance_blocks?: number
  token_dunnings?: number
  issuer_adaptive_backoffs?: number
  mandate_vitality_dunnings?: number
  action_counts?: Record<string, number>
}

type Audit = {
  decision_id: string
  payment_id: string
  rail: string
  issuer_bank?: string
  decline_code: string
  decline_iso_code?: string
  amount_paise: number
  attempt_number: number
  action: string
  decline_kind: string
  recoverability: number
  classification_source: string
  constraint_hits: Array<{ code: string; message: string }>
  reason_chain: string[]
  execution_result?: string
  issuer_health_level?: string
  mandate_vitality_level?: string
  feature_importance?: Record<string, number>
}

type BatchResult = {
  railwise: Metrics
  baseline: Metrics
  sample_audits: Audit[]
  issuer_health?: Record<string, { health: string; td_rate: number; sample_size: number }>
  lift: { soft_recovery_rate_delta: number; recovered_paise_delta: number }
}

type StabilityResult = {
  n_seeds: number
  seeds: Array<{ seed: number; soft_delta_pp: number; railwise_soft_recovery: number; recovered_delta_paise: number }>
  summary: {
    railwise_wins_soft_rate: number
    avg_soft_delta_pp: number
    std_soft_delta_pp: number
    railwise_soft_recovery_mean: number
    railwise_soft_recovery_std: number
    zero_hard_wasted_all_seeds: boolean
    zero_upi_violations_all_seeds: boolean
  }
}

type TrainingResult = {
  metrics: { accuracy: number; soft_recall: number; hard_recall: number }
  feature_weights: Array<{ feature: string; weight: number; direction: string }>
  audit_trail: Array<{ step: string; detail: string }>
  quality_passed: boolean
  training_stability?: { runs: Array<{ train_seed: number; accuracy: number }>; summary: { accuracy_mean: number; accuracy_std: number } }
}

type EdgeCase = {
  id: string
  title: string
  notes: string
  expected_action?: string
  expected_constraint?: string | null
  fixture: Record<string, unknown> & {
    method?: string
    amount?: number
    issuer_bank?: string
    attempt_number?: number
    error?: { code?: string; iso_code?: string }
  }
  decision: {
    action: string
    delay_minutes?: number | null
    classification?: {
      decline_kind?: string
      recoverability?: number
      source?: string
      feature_importance?: Record<string, number>
    }
    constraint_hits?: Array<{ code: string; message: string }>
    reason_chain?: string[]
    execution_result?: string
    recovered_amount_paise?: number
    issuer_health_level?: string
    mandate_vitality_level?: string
  }
}

type SandboxCompare = {
  full_railwise: { action: string; classification: { recoverability: number; source: string }; constraint_hits: Array<{ code: string }>; reason_chain: string[] }
  your_config: { action: string; classification: { recoverability: number; source: string }; constraint_hits: Array<{ code: string }>; reason_chain: string[] }
  action_changed: boolean
  diffs: { action: { full: string; custom: string }; recoverability: { full: number; custom: number } }
}

type AblationResult = {
  variants: Array<{ variant: string; metrics: Metrics }>
  comparisons: Array<{ variant: string; vs_full: Record<string, number> }>
}

type View = 'journey' | 'overview' | 'sandbox' | 'stability' | 'model' | 'edges'

type Toggles = {
  use_ml_model: boolean
  use_compliance_blocks: boolean
  use_issuer_health: boolean
  use_mandate_vitality: boolean
  use_timing_ai: boolean
}

const DEFAULT_TOGGLES: Toggles = {
  use_ml_model: true,
  use_compliance_blocks: true,
  use_issuer_health: true,
  use_mandate_vitality: true,
  use_timing_ai: true,
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function inr(paise: number) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100)
}

function pct(n: number) {
  return `${(n * 100).toFixed(1)}%`
}

function actionClass(action: string) {
  return `action-badge action-${action}`
}

// ── App ──────────────────────────────────────────────────────────────────────

function hasValidTraining(t: TrainingResult | null | undefined): t is TrainingResult {
  return !!(t && t.metrics && typeof t.metrics.accuracy === 'number')
}

export default function App() {
  const [view, setView] = useState<View>('journey')
  const [batch, setBatch] = useState<BatchResult | null>(null)
  const [stability, setStability] = useState<StabilityResult | null>(null)
  const [training, setTraining] = useState<TrainingResult | null>(null)
  const [ablation, setAblation] = useState<AblationResult | null>(null)
  const [edgeCases, setEdgeCases] = useState<EdgeCase[]>([])
  const [selectedEdge, setSelectedEdge] = useState<EdgeCase | null>(null)
  const [sandboxResult, setSandboxResult] = useState<SandboxCompare | null>(null)
  const [toggles, setToggles] = useState<Toggles>(DEFAULT_TOGGLES)
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [aiUsage, setAiUsage] = useState<Array<{ name: string; uses_ai: boolean; why: string }>>([])
  const [edgesError, setEdgesError] = useState<string | null>(null)

  async function loadEdges() {
    setLoading('edges')
    setEdgesError(null)
    try {
      const res = await fetch(`${API}/edge-cases`)
      if (!res.ok) throw new Error(`Edge cases failed (${res.status}). Is the backend on :8000?`)
      const data = await res.json()
      const list = Array.isArray(data.edge_cases) ? data.edge_cases : []
      setEdgeCases(list)
      if (!list.length) setEdgesError('No edge cases returned from API')
      else if (!selectedEdge && list[0]) setSelectedEdge(list[0])
    } catch (e) {
      setEdgeCases([])
      setEdgesError(e instanceof Error ? e.message : 'Failed to load edge cases. Is the backend on :8000?')
    } finally {
      setLoading(null)
    }
  }

  useEffect(() => {
    loadEdges()
    fetch(`${API}/batch/latest`).then((r) => r.json()).then((d) => d?.railwise && setBatch(d)).catch(() => {})
    fetch(`${API}/ai/usage`).then((r) => r.json()).then((d) => setAiUsage(d.layers || [])).catch(() => {})
    fetch(`${API}/model/training/latest`)
      .then((r) => r.json())
      .then((d) => { if (hasValidTraining(d)) setTraining(d) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (view === 'edges' && edgeCases.length === 0 && !edgesError) {
      loadEdges()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])

  useEffect(() => {
    if (selectedEdge && view === 'sandbox') {
      testSandbox(selectedEdge.fixture)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toggles])

  async function runBatch() {
    setLoading('batch')
    setError(null)
    try {
      const res = await fetch(`${API}/batch/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ n: 500, seed: 2025 }) })
      if (!res.ok) throw new Error('Batch failed')
      setBatch(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error')
    } finally {
      setLoading(null)
    }
  }

  async function runStability() {
    setLoading('stability')
    setError(null)
    try {
      const res = await fetch(`${API}/analytics/stability`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ n_seeds: 30, batch_size: 500 }) })
      setStability(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error')
    } finally {
      setLoading(null)
    }
  }

  async function runTraining(withStability = true) {
    setLoading('train')
    setError(null)
    try {
      const res = await fetch(`${API}/model/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ train_seed: 7, n_train: 6000, n_test: 1000, stability_runs: withStability ? 5 : 0 }),
      })
      if (!res.ok) {
        const err = await res.text()
        throw new Error(err || `Training failed (${res.status})`)
      }
      const data = await res.json()
      if (!hasValidTraining(data)) throw new Error('Training returned invalid metrics')
      setTraining(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Training failed. Is the backend running on :8000?')
    } finally {
      setLoading(null)
    }
  }

  async function runAblation() {
    setLoading('ablation')
    try {
      const res = await fetch(`${API}/analytics/ablation`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ batch_size: 200, seed: 42 }) })
      setAblation(await res.json())
    } finally {
      setLoading(null)
    }
  }

  async function testSandbox(fixture: Record<string, unknown>) {
    setLoading('sandbox')
    try {
      const res = await fetch(`${API}/sandbox/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event: fixture, ...toggles, compare_all_variants: false }),
      })
      setSandboxResult(await res.json())
    } finally {
      setLoading(null)
    }
  }

  function selectEdge(ec: EdgeCase) {
    setSelectedEdge(ec)
    testSandbox(ec.fixture)
  }

  function toggle(key: keyof Toggles) {
    setToggles((t) => ({ ...t, [key]: !t[key] }))
  }

  return (
    <div className="dashboard">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-icon">◈</span>
          <div>
            <div className="brand-name">Railwise</div>
            <div className="brand-sub">Recovery Control Panel</div>
          </div>
        </div>
        <nav className="sidebar-nav">
          {(['journey', 'overview', 'sandbox', 'stability', 'model', 'edges'] as View[]).map((v) => (
            <button key={v} className={`nav-item ${view === v ? 'active' : ''}`} onClick={() => setView(v)}>
              {v === 'journey' && '▸ Recovery Journey'}
              {v === 'overview' && '◉ Overview'}
              {v === 'sandbox' && '⚙ Sandbox Lab'}
              {v === 'stability' && '◎ Stability'}
              {v === 'model' && '◈ Model Lab'}
              {v === 'edges' && '◇ Edge Cases'}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="status-dot live" />
          Track 03 · AI Revenue Recovery
        </div>
      </aside>

      {/* Main */}
      <main className="main">
        <header className="topbar">
          <h1>
            {view === 'journey' && 'Mandate Recovery Journey'}
            {view === 'overview' && 'Payment Recovery Dashboard'}
            {view === 'sandbox' && 'Ablation Sandbox'}
            {view === 'stability' && 'Multi-Seed Stability'}
            {view === 'model' && 'Model Training Lab'}
            {view === 'edges' && 'Edge Case Gallery'}
          </h1>
          <div className="topbar-actions">
            {view === 'overview' && (
              <button className="btn-primary" disabled={!!loading} onClick={runBatch}>
                {loading === 'batch' ? 'Running…' : 'Run A/B Batch'}
              </button>
            )}
            {view === 'stability' && (
              <button className="btn-primary" disabled={!!loading} onClick={runStability}>
                {loading === 'stability' ? 'Running 30 seeds…' : 'Run 30-Seed Stability'}
              </button>
            )}
            {view === 'model' && (
              <button className="btn-primary" disabled={!!loading} onClick={() => runTraining(true)}>
                {loading === 'train' ? 'Training…' : 'Train Model Live'}
              </button>
            )}
            {view === 'edges' && (
              <button className="btn-primary" disabled={loading === 'edges'} onClick={loadEdges}>
                {loading === 'edges' ? 'Loading…' : 'Reload edge cases'}
              </button>
            )}
          </div>
        </header>

        {error && <div className="alert alert-error">{error}</div>}

        {/* RECOVERY JOURNEY */}
        {view === 'journey' && (
          <div className="view-content journey-wrap">
            <AutopayJourney />
          </div>
        )}

        {/* OVERVIEW */}
        {view === 'overview' && (
          <div className="view-content">
            {batch ? (
              <>
                <div className="kpi-grid">
                  <KpiCard label="Soft Recovery (Railwise)" value={pct(batch.railwise.soft_recovery_rate)} delta={`+${pct(batch.lift.soft_recovery_rate_delta)} vs baseline`} positive />
                  <KpiCard label="₹ Recovered Lift" value={inr(batch.lift.recovered_paise_delta)} delta={`Total ${inr(batch.railwise.recovered_paise)}`} positive />
                  <KpiCard label="Hard Wasted Retries" value={String(batch.railwise.hard_decline_wasted_retries)} delta="Must be 0" positive={batch.railwise.hard_decline_wasted_retries === 0} />
                  <KpiCard label="UPI Violations" value={String(batch.railwise.upi_cooldown_violations)} delta="Must be 0" positive={batch.railwise.upi_cooldown_violations === 0} />
                  <KpiCard label="PDN Blocks" value={String(batch.railwise.pdn_compliance_blocks ?? 0)} delta="RBI compliance" />
                  <KpiCard label="Issuer Backoffs" value={String(batch.railwise.issuer_adaptive_backoffs ?? 0)} delta="Thundering herd defense" />
                </div>

                {batch.issuer_health && (
                  <section className="card">
                    <h2>Issuer Health Monitor</h2>
                    <div className="issuer-grid">
                      {Object.entries(batch.issuer_health).map(([bank, info]) => (
                        <div key={bank} className={`issuer-chip health-${info.health}`}>
                          <span className="issuer-name">{bank.toUpperCase()}</span>
                          <span className="issuer-health">{info.health}</span>
                          <span className="issuer-td">TD {(info.td_rate * 100).toFixed(1)}%</span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                <section className="card">
                  <h2>Live Failure Feed</h2>
                  <div className="failure-grid">
                    {(batch.sample_audits || []).slice(0, 12).map((a) => (
                      <FailureCard key={a.decision_id} audit={a} />
                    ))}
                  </div>
                </section>
              </>
            ) : (
              <EmptyState message="Run an A/B batch to populate the dashboard" action={runBatch} actionLabel="Run Batch" />
            )}
          </div>
        )}

        {/* SANDBOX */}
        {view === 'sandbox' && (
          <div className="view-content sandbox-layout">
            <section className="card toggles-card">
              <h2>Engine layers</h2>
              <div className="toggle-grid">
                <Toggle label="ML Model" sub="Ambiguous decline classifier" on={toggles.use_ml_model} onChange={() => toggle('use_ml_model')} />
                <Toggle label="Compliance Blocks" sub="NPCI/RBI hard gate" on={toggles.use_compliance_blocks} onChange={() => toggle('use_compliance_blocks')} />
                <Toggle label="Issuer Health" sub="Cross-customer outage backoff" on={toggles.use_issuer_health} onChange={() => toggle('use_issuer_health')} />
                <Toggle label="Mandate Vitality" sub="Proactive mandate death" on={toggles.use_mandate_vitality} onChange={() => toggle('use_mandate_vitality')} />
                <Toggle label="Timing AI" sub="Payday / non-peak slots" on={toggles.use_timing_ai} onChange={() => toggle('use_timing_ai')} />
              </div>
              <button className="btn-secondary" onClick={runAblation} disabled={!!loading}>
                {loading === 'ablation' ? 'Running ablation…' : 'Run ablation (200 events)'}
              </button>
            </section>

            <div className="sandbox-body">
              <section className="card">
                <h2>Pick a failure to test</h2>
                <div className="edge-picker">
                  {edgeCases.map((ec) => (
                    <button key={ec.id} className={`edge-btn ${selectedEdge?.id === ec.id ? 'selected' : ''}`} onClick={() => selectEdge(ec)}>
                      {ec.title}
                    </button>
                  ))}
                </div>
              </section>

              {sandboxResult?.full_railwise && sandboxResult?.your_config && (
                <section className="card compare-card">
                  <h2>Decision Comparison</h2>
                  {sandboxResult.action_changed && <div className="alert alert-warn">Action changed after a layer was toggled.</div>}
                  <div className="compare-columns">
                    <CompareCol
                      title="Full Railwise"
                      action={sandboxResult.full_railwise.action}
                      recov={sandboxResult.full_railwise.classification?.recoverability ?? 0}
                      source={sandboxResult.full_railwise.classification?.source || '—'}
                      constraints={sandboxResult.full_railwise.constraint_hits || []}
                      chain={sandboxResult.full_railwise.reason_chain || []}
                      highlight
                    />
                    <CompareCol
                      title="Your Config"
                      action={sandboxResult.your_config.action}
                      recov={sandboxResult.your_config.classification?.recoverability ?? 0}
                      source={sandboxResult.your_config.classification?.source || '—'}
                      constraints={sandboxResult.your_config.constraint_hits || []}
                      chain={sandboxResult.your_config.reason_chain || []}
                      changed={sandboxResult.action_changed}
                    />
                  </div>
                </section>
              )}

              {ablation && (
                <section className="card">
                  <h2>Ablation Results (batch)</h2>
                  <table className="data-table">
                    <thead>
                      <tr><th>Variant</th><th>Soft Recovery</th><th>₹ Recovered</th><th>Hard Waste</th><th>UPI Viol.</th></tr>
                    </thead>
                    <tbody>
                      {ablation.variants.map((v) => (
                        <tr key={v.variant} className={v.variant === 'full_railwise' ? 'row-highlight' : ''}>
                          <td>{v.variant}</td>
                          <td>{pct(v.metrics.soft_recovery_rate)}</td>
                          <td>{inr(v.metrics.recovered_paise)}</td>
                          <td>{v.metrics.hard_decline_wasted_retries}</td>
                          <td>{v.metrics.upi_cooldown_violations}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}
            </div>
          </div>
        )}

        {/* STABILITY */}
        {view === 'stability' && (
          <div className="view-content">
            {stability ? (
              <>
                <div className="kpi-grid">
                  <KpiCard label="Wins vs Baseline" value={`${stability.summary.railwise_wins_soft_rate}/${stability.n_seeds}`} delta="soft recovery rate" positive />
                  <KpiCard label="Avg Lift" value={`+${stability.summary.avg_soft_delta_pp} pp`} delta={`σ=${stability.summary.std_soft_delta_pp} pp`} positive />
                  <KpiCard label="Recovery Mean" value={pct(stability.summary.railwise_soft_recovery_mean)} delta={`σ=${(stability.summary.railwise_soft_recovery_std * 100).toFixed(2)}%`} />
                  <KpiCard label="Compliance" value={stability.summary.zero_hard_wasted_all_seeds && stability.summary.zero_upi_violations_all_seeds ? 'PASS' : 'FAIL'} delta="0 violations all seeds" positive={stability.summary.zero_hard_wasted_all_seeds} />
                </div>
                <section className="card">
                  <h2>Per-Seed Audit Trail ({stability.n_seeds} seeds)</h2>
                  <div className="stability-bars">
                    {stability.seeds.map((s) => (
                      <div key={s.seed} className="stability-row" title={`Seed ${s.seed}: Δ${s.soft_delta_pp}pp`}>
                        <span className="seed-label">{s.seed}</span>
                        <div className="bar-track">
                          <div className={`bar-fill ${s.soft_delta_pp >= 0 ? 'positive' : 'negative'}`} style={{ width: `${Math.min(100, Math.abs(s.soft_delta_pp) * 10)}%` }} />
                        </div>
                        <span className={`delta-label ${s.soft_delta_pp >= 0 ? 'pos' : 'neg'}`}>{s.soft_delta_pp >= 0 ? '+' : ''}{s.soft_delta_pp}pp</span>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            ) : (
              <EmptyState message="Run 30 seeds to measure lift variance." action={runStability} actionLabel="Run Stability" />
            )}
          </div>
        )}

        {/* MODEL LAB */}
        {view === 'model' && (
          <div className="view-content">
            {loading === 'train' && (
              <div className="alert alert-warn">Training across 5 seeds…</div>
            )}
            {hasValidTraining(training) ? (
              <>
                <div className="kpi-grid">
                  <KpiCard label="Accuracy" value={pct(training.metrics.accuracy)} delta="held-out test" positive={training.metrics.accuracy >= 0.85} />
                  <KpiCard label="Soft Recall" value={pct(training.metrics.soft_recall)} delta="recoverable declines" positive />
                  <KpiCard label="Hard Recall" value={pct(training.metrics.hard_recall)} delta="non-retryable declines" positive />
                  <KpiCard label="Quality Gate" value={training.quality_passed ? 'PASSED' : 'FAILED'} delta="acc 87 to 94%, hard recall ≥ 65%" positive={training.quality_passed} />
                </div>

                <div className="two-col">
                  <section className="card">
                    <h2>Training Audit Trail</h2>
                    <div className="audit-trail">
                      {(training.audit_trail || []).map((step, i) => (
                        <div key={i} className="audit-step">
                          <span className="step-num">{i + 1}</span>
                          <div>
                            <strong>{step.step}</strong>
                            <p>{step.detail}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                  <section className="card">
                    <h2>Feature weights</h2>
                    <div className="weight-list">
                      {(training.feature_weights || []).map((fw) => (
                        <div key={fw.feature} className="weight-row">
                          <span>{fw.feature}</span>
                          <span className={`weight-val ${fw.direction}`}>{fw.weight > 0 ? '+' : ''}{fw.weight.toFixed(3)} → {fw.direction}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                </div>

                {training.training_stability && (
                  <section className="card">
                    <h2>Training Stability ({training.training_stability.runs.length} seeds)</h2>
                    <p className="muted">Accuracy mean {pct(training.training_stability.summary.accuracy_mean)} · σ={(training.training_stability.summary.accuracy_std * 100).toFixed(2)}%</p>
                    <div className="stability-bars compact">
                      {training.training_stability.runs.map((r) => (
                        <div key={r.train_seed} className="stability-row">
                          <span className="seed-label">s{r.train_seed}</span>
                          <div className="bar-track">
                            <div className="bar-fill positive" style={{ width: `${r.accuracy * 100}%` }} />
                          </div>
                          <span className="delta-label pos">{pct(r.accuracy)}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                <section className="card">
                  <h2>Where AI is used</h2>
                  <div className="ai-grid">
                    {aiUsage.map((layer) => (
                      <div key={layer.name} className={`ai-card ${layer.uses_ai ? 'ai-yes' : 'ai-no'}`}>
                        <div className="ai-badge">{layer.uses_ai ? 'AI' : 'Rules'}</div>
                        <strong>{layer.name}</strong>
                        <p>{layer.why}</p>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            ) : (
              <EmptyState message="Train the ambiguous-decline classifier live" action={() => runTraining(true)} actionLabel="Train Model" />
            )}
          </div>
        )}

        {/* EDGES */}
        {view === 'edges' && (
          <div className="view-content">
            {edgesError && (
              <div className="alert alert-error" style={{ margin: '0 0 16px' }}>
                {edgesError}
                <button className="btn-secondary" style={{ marginLeft: 12, width: 'auto' }} onClick={loadEdges}>
                  Retry
                </button>
              </div>
            )}

            {!edgesError && edgeCases.length === 0 && (
              <EmptyState
                message={loading === 'edges' ? 'Loading edge cases…' : 'Edge cases not loaded yet'}
                action={loadEdges}
                actionLabel="Load edge cases"
              />
            )}

            {edgeCases.length > 0 && (
              <div className="edges-layout">
                <section className="card edges-list-panel">
                  <h2>Named fixtures ({edgeCases.length})</h2>
                  <div className="edges-scroll">
                    {edgeCases.map((ec) => {
                      const recov = ec.decision?.classification?.recoverability
                      return (
                        <button
                          key={ec.id}
                          type="button"
                          className={`edge-row ${selectedEdge?.id === ec.id ? 'selected' : ''}`}
                          onClick={() => setSelectedEdge(ec)}
                        >
                          <div className="edge-row-top">
                            <span className="fc-rail">{(ec.fixture?.method || 'card').toUpperCase()}</span>
                            <span className={actionClass(ec.decision?.action || 'stop')}>{ec.decision?.action || '—'}</span>
                          </div>
                          <strong>{ec.title}</strong>
                          <span className="edge-row-meta">
                            {typeof recov === 'number' ? `recov ${recov.toFixed(2)}` : 'recov —'}
                            {' · '}
                            {ec.decision?.classification?.source || '—'}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </section>

                <section className="card edges-detail-panel">
                  {!selectedEdge && <div className="empty-state"><p>Select an edge case</p></div>}
                  {selectedEdge && (
                    <>
                      <div className="edges-detail-head">
                        <div>
                          <p className="product-eyebrow">{selectedEdge.id}</p>
                          <h2>{selectedEdge.title}</h2>
                          <p className="muted">{selectedEdge.notes}</p>
                        </div>
                        <span className={actionClass(selectedEdge.decision?.action || 'stop')}>
                          {selectedEdge.decision?.action}
                        </span>
                      </div>

                      <div className="fail-meta" style={{ marginTop: 16 }}>
                        <div>
                          <span>Rail</span>
                          <strong>{(selectedEdge.fixture?.method || 'card').toUpperCase()}</strong>
                        </div>
                        <div>
                          <span>Issuer</span>
                          <strong>{String(selectedEdge.fixture?.issuer_bank || '—').toUpperCase()}</strong>
                        </div>
                        <div>
                          <span>Decline</span>
                          <strong>
                            {selectedEdge.fixture?.error?.code || '—'}
                            {selectedEdge.fixture?.error?.iso_code ? ` · ISO ${selectedEdge.fixture.error.iso_code}` : ''}
                          </strong>
                        </div>
                        <div>
                          <span>Amount</span>
                          <strong>{inr(Number(selectedEdge.fixture?.amount || 0))}</strong>
                        </div>
                        <div>
                          <span>Attempt</span>
                          <strong>{String(selectedEdge.fixture?.attempt_number ?? '—')}</strong>
                        </div>
                        <div>
                          <span>Expected</span>
                          <strong>
                            {selectedEdge.expected_action || '—'}
                            {selectedEdge.expected_constraint ? ` · ${selectedEdge.expected_constraint}` : ''}
                          </strong>
                        </div>
                      </div>

                      <div className="path-metrics" style={{ marginTop: 14 }}>
                        <div className="path-metric">
                          <span>Kind</span>
                          <strong>{selectedEdge.decision?.classification?.decline_kind || '—'}</strong>
                        </div>
                        <div className="path-metric">
                          <span>Recoverability</span>
                          <strong>
                            {typeof selectedEdge.decision?.classification?.recoverability === 'number'
                              ? selectedEdge.decision.classification.recoverability.toFixed(3)
                              : '—'}
                          </strong>
                        </div>
                        <div className="path-metric">
                          <span>Source</span>
                          <strong>{selectedEdge.decision?.classification?.source || '—'}</strong>
                        </div>
                        <div className="path-metric">
                          <span>Delay</span>
                          <strong>
                            {selectedEdge.decision?.delay_minutes != null
                              ? `${Math.round(selectedEdge.decision.delay_minutes)}m`
                              : '—'}
                          </strong>
                        </div>
                      </div>

                      {(selectedEdge.decision?.constraint_hits || []).length > 0 && (
                        <div style={{ marginTop: 16 }}>
                          <h3 style={{ fontSize: '0.9rem', marginBottom: 8 }}>Constraints</h3>
                          {(selectedEdge.decision?.constraint_hits || []).map((h) => (
                            <div key={h.code} className="constraint-block">
                              <strong>{h.code}</strong>
                              <p>{h.message}</p>
                            </div>
                          ))}
                        </div>
                      )}

                      {(selectedEdge.decision?.reason_chain || []).length > 0 && (
                        <div style={{ marginTop: 16 }}>
                          <h3 style={{ fontSize: '0.9rem', marginBottom: 8 }}>Reason chain</h3>
                          <ol className="reason-list">
                            {(selectedEdge.decision?.reason_chain || []).map((step, i) => (
                              <li key={`${step}-${i}`}>{step}</li>
                            ))}
                          </ol>
                        </div>
                      )}

                      <div className="ws-nav" style={{ marginTop: 18 }}>
                        <button
                          className="btn-secondary"
                          style={{ width: 'auto' }}
                          onClick={() => {
                            setView('sandbox')
                            selectEdge(selectedEdge)
                          }}
                        >
                          Open in Sandbox Lab
                        </button>
                      </div>
                    </>
                  )}
                </section>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────

function KpiCard({ label, value, delta, positive }: { label: string; value: string; delta: string; positive?: boolean }) {
  return (
    <div className={`kpi-card ${positive ? 'kpi-good' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-delta">{delta}</div>
    </div>
  )
}

function FailureCard({ audit }: { audit: Audit }) {
  return (
    <div className="failure-card">
      <div className="fc-header">
        <span className="fc-rail">{audit.rail.toUpperCase()}</span>
        <span className="fc-issuer">{audit.issuer_bank?.toUpperCase()}</span>
        <span className={actionClass(audit.action)}>{audit.action}</span>
      </div>
      <div className="fc-amount">{inr(audit.amount_paise)}</div>
      <div className="fc-decline">
        {audit.decline_code}
        {audit.decline_iso_code && <span className="iso">ISO {audit.decline_iso_code}</span>}
      </div>
      <div className="fc-meta">
        Attempt {audit.attempt_number} · {audit.decline_kind} · recov {(audit.recoverability * 100).toFixed(0)}%
      </div>
      {audit.constraint_hits?.length > 0 && (
        <div className="fc-constraints">
          {audit.constraint_hits.slice(0, 2).map((h) => (
            <span key={h.code} className="constraint-tag">{h.code}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function Toggle({ label, sub, on, onChange }: { label: string; sub: string; on: boolean; onChange: () => void }) {
  return (
    <label className="toggle-item">
      <div>
        <strong>{label}</strong>
        <span>{sub}</span>
      </div>
      <button type="button" className={`toggle-switch ${on ? 'on' : 'off'}`} onClick={onChange} aria-pressed={on}>
        <span className="toggle-knob" />
      </button>
    </label>
  )
}

function CompareCol({ title, action, recov, source, constraints, chain, highlight, changed }: {
  title: string; action: string; recov: number; source: string
  constraints: Array<{ code: string }>; chain: string[]
  highlight?: boolean; changed?: boolean
}) {
  return (
    <div className={`compare-col ${highlight ? 'highlight' : ''} ${changed ? 'changed' : ''}`}>
      <h3>{title}</h3>
      <div className={actionClass(action)}>{action}</div>
      <p>Recoverability: <strong>{recov.toFixed(3)}</strong> · {source}</p>
      <div className="constraint-tags">
        {constraints.map((c) => <span key={c.code} className="constraint-tag">{c.code}</span>)}
        {constraints.length === 0 && <span className="muted">No constraints fired</span>}
      </div>
      <details>
        <summary>Reason chain ({chain.length} steps)</summary>
        <ol className="reason-list">{chain.map((s, i) => <li key={i}>{s}</li>)}</ol>
      </details>
    </div>
  )
}

function EmptyState({ message, action, actionLabel }: { message: string; action: () => void; actionLabel: string }) {
  return (
    <div className="empty-state">
      <p>{message}</p>
      <button className="btn-primary" onClick={action}>{actionLabel}</button>
    </div>
  )
}
