"use client";
import { useEffect, useState } from "react";

interface AgentEvent {
  event_id: string;
  repo: string;
  status: string;
  triage?: { failure_type: string; confidence: number; summary: string };
  policy?: { decision: string };
  pr?: { url: string; number: number };
  errors?: any[];
  timeline?: any[];
  rag_evaluation?: {
    grade?: { letter: string; score: number; retrieval_score: number; context_score: number; generation_score: number };
    retrieval?: { hit_rate: number; mean_similarity: number; mrr: number; recall_at_k: number; result_count: number };
    context_quality?: { failure_type_match_rate: number; context_diversity: number; duplicate_ratio: number };
    generation_impact?: { rag_value_score: number; type_alignment: boolean; grounding_rate: number };
    end_to_end?: { rag_latency_pct: number; retrieval_latency_ms: number };
  };
  llm_summary?: {
    total_calls: number;
    successful_calls: number;
    failed_calls: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_cost_usd: number;
    total_latency_ms: number;
    by_agent: Record<string, {
      calls: number; total_tokens: number; prompt_tokens: number;
      completion_tokens: number; cost_usd: number; latency_ms: number;
    }>;
  };
  judge?: {
    factuality_score: number;
    completeness_score: number;
    confidence_calibration: number;
    hallucination_flag: boolean;
    issues: string[];
    overall_score: number;
    overall_grade: string;
    verdict_summary: string;
  };
}

const GRADE_COLORS: Record<string, string> = {
  A: "#22c55e",
  B: "#84cc16",
  C: "#eab308",
  D: "#f97316",
  F: "#ef4444",
};

const STATUS_COLORS: Record<string, string> = {
  completed: "#22c55e",
  denied: "#f59e0b",
  failed: "#ef4444",
  halted: "#6b7280",
  running: "#3b82f6",
  quality_blocked: "#f97316",
};

const AGENT_STEPS = [
  { id: "evidence", label: "Evidence Retrieval", icon: "🔍" },
  { id: "triage", label: "Failure Triage", icon: "🏷️" },
  { id: "planner", label: "Fix Planner", icon: "📋" },
  { id: "solver", label: "Code Solver", icon: "🤖" },
  { id: "validator", label: "Validator Review", icon: "✅" },
  { id: "policy", label: "Policy Gate", icon: "🛡️" },
  { id: "pr", label: "PR Created", icon: "🔀" },
];

export default function Dashboard() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [selected, setSelected] = useState<AgentEvent | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchEvents() {
      try {
        const res = await fetch("/api/events");
        if (res.ok) {
          const data = await res.json();
          setEvents(data.events || []);
          if (data.events?.length > 0) setSelected(data.events[0]);
        }
      } catch {
        setEvents(MOCK_EVENTS);
        setSelected(MOCK_EVENTS[0]);
      } finally {
        setLoading(false);
      }
    }
    fetchEvents();
    const interval = setInterval(fetchEvents, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1100, margin: "0 auto", padding: "2rem 1rem" }}>
      <header style={{ marginBottom: "2rem" }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>🤖 RepoMind</h1>
        <p style={{ color: "#6b7280", marginTop: 4, fontSize: 14 }}>
          AI-powered CI Auto-Fix Agent — Agent Swarms Architecture
        </p>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, marginBottom: 24 }}>
        {[
          { label: "Total Events", value: events.length },
          { label: "Fixed (PRs Created)", value: events.filter(e => e.pr?.url).length },
          { label: "Policy Denied", value: events.filter(e => e.policy?.decision === "deny").length },
          { label: "Errors", value: events.filter(e => e.status === "failed").length },
          {
            label: "Avg RAG Grade",
            value: (() => {
              const grades = events.map(e => e.rag_evaluation?.grade?.letter).filter(Boolean) as string[];
              if (grades.length === 0) return "—";
              const points: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
              const avg = grades.reduce((s, g) => s + (points[g] ?? 0), 0) / grades.length;
              if (avg >= 3.5) return "A";
              if (avg >= 2.5) return "B";
              if (avg >= 1.5) return "C";
              if (avg >= 0.5) return "D";
              return "F";
            })(),
          },
          {
            label: "Total LLM Cost",
            value: (() => {
              const total = events.reduce((s, e) => s + (e.llm_summary?.total_cost_usd ?? 0), 0);
              return total > 0 ? `$${total.toFixed(4)}` : "—";
            })(),
          },
        ].map(stat => (
          <div key={stat.label} style={{ background: "#f9fafb", borderRadius: 8, padding: "12px 16px", border: "1px solid #e5e7eb" }}>
            <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 4 }}>{stat.label}</div>
            <div style={{
              fontSize: 22, fontWeight: 600,
              color: stat.label === "Avg RAG Grade" && typeof stat.value === "string" ? (GRADE_COLORS[stat.value as string] || "#111827") : "#111827",
            }}>{stat.value}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 16 }}>
        <div style={{ border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid #e5e7eb", fontSize: 13, fontWeight: 500, background: "#f9fafb" }}>
            Recent Events
          </div>
          {loading ? (
            <div style={{ padding: 16, color: "#6b7280", fontSize: 13 }}>Loading...</div>
          ) : events.length === 0 ? (
            <div style={{ padding: 16, color: "#6b7280", fontSize: 13 }}>No events yet. Connect a GitHub webhook to start.</div>
          ) : (
            events.map(evt => (
              <div
                key={evt.event_id}
                onClick={() => setSelected(evt)}
                style={{
                  padding: "12px 16px",
                  cursor: "pointer",
                  borderBottom: "1px solid #f3f4f6",
                  background: selected?.event_id === evt.event_id ? "#eff6ff" : "white",
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>{evt.repo}</div>
                <div style={{ fontSize: 11, color: "#6b7280", marginBottom: 4 }}>{evt.triage?.failure_type || "unknown"}</div>
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <span style={{
                    fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 99,
                    background: STATUS_COLORS[evt.status] + "22",
                    color: STATUS_COLORS[evt.status],
                    textTransform: "uppercase",
                  }}>
                    {evt.status}
                  </span>
                  {evt.rag_evaluation?.grade?.letter && (
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 99,
                      background: (GRADE_COLORS[evt.rag_evaluation.grade.letter] || "#6b7280") + "22",
                      color: GRADE_COLORS[evt.rag_evaluation.grade.letter] || "#6b7280",
                    }}>
                      RAG {evt.rag_evaluation.grade.letter}
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {selected && (
          <div style={{ border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid #e5e7eb", background: "#f9fafb" }}>
              <div style={{ fontSize: 15, fontWeight: 600 }}>{selected.repo}</div>
              <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{selected.event_id}</div>
            </div>
            <div style={{ padding: "20px" }}>
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: "#6b7280", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>Agent Pipeline</div>
                <div style={{ display: "flex", gap: 0, alignItems: "center" }}>
                  {AGENT_STEPS.map((step, i) => {
                    const isActive = selected.status !== "failed";
                    const isDone = isActive;
                    return (
                      <div key={step.id} style={{ display: "flex", alignItems: "center" }}>
                        <div style={{
                          width: 36, height: 36, borderRadius: "50%", display: "flex", alignItems: "center",
                          justifyContent: "center", fontSize: 16,
                          background: isDone ? "#eff6ff" : "#f9fafb",
                          border: `2px solid ${isDone ? "#3b82f6" : "#e5e7eb"}`,
                          title: step.label,
                        }}>
                          {step.icon}
                        </div>
                        {i < AGENT_STEPS.length - 1 && (
                          <div style={{ width: 20, height: 2, background: isDone ? "#3b82f6" : "#e5e7eb" }} />
                        )}
                      </div>
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: 0, marginTop: 4 }}>
                  {AGENT_STEPS.map((step, i) => (
                    <div key={step.id} style={{ width: i < AGENT_STEPS.length - 1 ? 56 : 36, fontSize: 9, color: "#6b7280", textAlign: "center" }}>
                      {step.label}
                    </div>
                  ))}
                </div>
              </div>

              {selected.triage && (
                <div style={{ background: "#f9fafb", borderRadius: 8, padding: "12px 16px", marginBottom: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 8 }}>🏷️ Triage Result</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 13 }}>
                    <div><span style={{ color: "#6b7280" }}>Type: </span>{selected.triage.failure_type}</div>
                    <div><span style={{ color: "#6b7280" }}>Confidence: </span>{(selected.triage.confidence * 100).toFixed(0)}%</div>
                    <div style={{ gridColumn: "1/-1" }}><span style={{ color: "#6b7280" }}>Summary: </span>{selected.triage.summary}</div>
                  </div>
                </div>
              )}

              {selected.rag_evaluation?.grade && (
                <div style={{
                  background: "#f9fafb",
                  border: `1px solid ${GRADE_COLORS[selected.rag_evaluation.grade.letter] || "#e5e7eb"}33`,
                  borderRadius: 8,
                  padding: "12px 16px",
                  marginBottom: 12,
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 500 }}>📊 RAG Quality Evaluation</div>
                    <div style={{
                      fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 99,
                      background: (GRADE_COLORS[selected.rag_evaluation.grade.letter] || "#6b7280") + "22",
                      color: GRADE_COLORS[selected.rag_evaluation.grade.letter] || "#6b7280",
                    }}>
                      Grade {selected.rag_evaluation.grade.letter} · {(selected.rag_evaluation.grade.score * 100).toFixed(0)}%
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, fontSize: 12, marginBottom: 10 }}>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Retrieval</div>
                      <div style={{ fontWeight: 600 }}>{(selected.rag_evaluation.grade.retrieval_score * 100).toFixed(0)}%</div>
                    </div>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Context</div>
                      <div style={{ fontWeight: 600 }}>{(selected.rag_evaluation.grade.context_score * 100).toFixed(0)}%</div>
                    </div>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Generation</div>
                      <div style={{ fontWeight: 600 }}>{(selected.rag_evaluation.grade.generation_score * 100).toFixed(0)}%</div>
                    </div>
                  </div>
                  {selected.rag_evaluation.retrieval && (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, fontSize: 11, color: "#374151" }}>
                      <div><span style={{ color: "#6b7280" }}>Hit: </span>{(selected.rag_evaluation.retrieval.hit_rate * 100).toFixed(0)}%</div>
                      <div><span style={{ color: "#6b7280" }}>MRR: </span>{selected.rag_evaluation.retrieval.mrr.toFixed(2)}</div>
                      <div><span style={{ color: "#6b7280" }}>Sim: </span>{selected.rag_evaluation.retrieval.mean_similarity.toFixed(2)}</div>
                      <div><span style={{ color: "#6b7280" }}>Found: </span>{selected.rag_evaluation.retrieval.result_count}</div>
                    </div>
                  )}
                  {selected.rag_evaluation.end_to_end && (
                    <div style={{ marginTop: 6, fontSize: 10, color: "#6b7280" }}>
                      RAG latency: {selected.rag_evaluation.end_to_end.retrieval_latency_ms.toFixed(0)}ms
                      {" "}({selected.rag_evaluation.end_to_end.rag_latency_pct.toFixed(1)}% of pipeline)
                    </div>
                  )}
                </div>
              )}

              {selected.llm_summary && selected.llm_summary.total_calls > 0 && (
                <div style={{ background: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: 8, padding: "12px 16px", marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 500 }}>💰 LLM Cost & Tokens</div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "#6366f1" }}>
                      ${selected.llm_summary.total_cost_usd.toFixed(4)} · {selected.llm_summary.total_tokens.toLocaleString()} tok · {selected.llm_summary.total_calls} call{selected.llm_summary.total_calls !== 1 ? "s" : ""}
                    </div>
                  </div>
                  {/* Stacked bar — tokens per agent */}
                  {(() => {
                    const total = selected.llm_summary!.total_tokens || 1;
                    const colors: Record<string, string> = {
                      triage: "#3b82f6", planner: "#8b5cf6", solver: "#10b981", validator: "#f59e0b", judge: "#ec4899",
                    };
                    type AgentStat = { calls: number; total_tokens: number; prompt_tokens: number; completion_tokens: number; cost_usd: number; latency_ms: number };
                    const entries = Object.entries(selected.llm_summary!.by_agent) as [string, AgentStat][];
                    return (
                      <>
                        <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", border: "1px solid #e5e7eb", marginBottom: 8 }}>
                          {entries.map(([agent, data]) => {
                            const pct = (data.total_tokens / total) * 100;
                            return (
                              <div
                                key={agent}
                                title={`${agent}: ${data.total_tokens} tokens`}
                                style={{ width: `${pct}%`, background: colors[agent] || "#9ca3af" }}
                              />
                            );
                          })}
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(entries.length, 5)}, 1fr)`, gap: 6, fontSize: 10 }}>
                          {entries.map(([agent, data]) => (
                            <div key={agent} style={{ background: "white", padding: "6px 8px", borderRadius: 4, border: "1px solid #e5e7eb" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 2, background: colors[agent] || "#9ca3af" }} />
                                <span style={{ fontWeight: 600, textTransform: "capitalize" }}>{agent}</span>
                              </div>
                              <div style={{ color: "#6b7280" }}>
                                {data.total_tokens.toLocaleString()} tok · ${data.cost_usd.toFixed(5)}
                              </div>
                            </div>
                          ))}
                        </div>
                      </>
                    );
                  })()}
                </div>
              )}

              {selected.judge && selected.judge.overall_grade && (
                <div style={{
                  background: "#f9fafb",
                  border: `1px solid ${GRADE_COLORS[selected.judge.overall_grade] || "#e5e7eb"}33`,
                  borderRadius: 8, padding: "12px 16px", marginBottom: 12,
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 500 }}>🛡️ LLM-as-Judge (Triage Quality)</div>
                    <div style={{
                      display: "flex", gap: 6, alignItems: "center",
                    }}>
                      {selected.judge.hallucination_flag && (
                        <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 99, background: "#fee2e2", color: "#b91c1c" }}>
                          ⚠️ HALLUCINATION
                        </span>
                      )}
                      <span style={{
                        fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 99,
                        background: (GRADE_COLORS[selected.judge.overall_grade] || "#6b7280") + "22",
                        color: GRADE_COLORS[selected.judge.overall_grade] || "#6b7280",
                      }}>
                        Grade {selected.judge.overall_grade} · {(selected.judge.overall_score * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, fontSize: 12, marginBottom: 8 }}>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Factuality</div>
                      <div style={{ fontWeight: 600 }}>{(selected.judge.factuality_score * 100).toFixed(0)}%</div>
                    </div>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Completeness</div>
                      <div style={{ fontWeight: 600 }}>{(selected.judge.completeness_score * 100).toFixed(0)}%</div>
                    </div>
                    <div style={{ background: "white", padding: "8px 10px", borderRadius: 6, border: "1px solid #e5e7eb" }}>
                      <div style={{ color: "#6b7280", fontSize: 10, marginBottom: 2 }}>Calibration</div>
                      <div style={{ fontWeight: 600 }}>{(selected.judge.confidence_calibration * 100).toFixed(0)}%</div>
                    </div>
                  </div>
                  {selected.judge.verdict_summary && (
                    <div style={{ fontSize: 11, color: "#374151", fontStyle: "italic" }}>
                      &ldquo;{selected.judge.verdict_summary}&rdquo;
                    </div>
                  )}
                  {selected.judge.issues && selected.judge.issues.length > 0 && (
                    <ul style={{ marginTop: 6, marginBottom: 0, paddingLeft: 18, fontSize: 11, color: "#b91c1c" }}>
                      {selected.judge.issues.slice(0, 3).map((issue, i) => (
                        <li key={i}>{issue}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {selected.pr?.url && (
                <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, padding: "12px 16px", marginBottom: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>🔀 PR Created</div>
                  <a href={selected.pr.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13, color: "#16a34a" }}>
                    View PR #{selected.pr.number} →
                  </a>
                </div>
              )}

              {selected.policy && (
                <div style={{
                  background: selected.policy.decision === "allow" ? "#f0fdf4" : "#fef9c3",
                  border: `1px solid ${selected.policy.decision === "allow" ? "#bbf7d0" : "#fde68a"}`,
                  borderRadius: 8, padding: "12px 16px", marginBottom: 12,
                }}>
                  <div style={{ fontSize: 13 }}>
                    🛡️ Policy: <strong>{selected.policy.decision === "allow" ? "Allowed ✅" : "Denied ⚠️"}</strong>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

const MOCK_EVENTS: AgentEvent[] = [
  {
    event_id: "evt-demo-repo-123-20260609T120000Z",
    repo: "org/service-api",
    status: "completed",
    triage: { failure_type: "dependency_error", confidence: 0.93, summary: "Missing 'httpx' package in requirements.txt" },
    policy: { decision: "allow" },
    pr: { url: "https://github.com/org/service-api/pull/42", number: 42 },
    rag_evaluation: {
      grade: { letter: "A", score: 0.91, retrieval_score: 0.94, context_score: 0.88, generation_score: 0.92 },
      retrieval: { hit_rate: 1.0, mean_similarity: 0.87, mrr: 1.0, recall_at_k: 1.0, result_count: 3 },
      context_quality: { failure_type_match_rate: 1.0, context_diversity: 1, duplicate_ratio: 0 },
      generation_impact: { rag_value_score: 0.92, type_alignment: true, grounding_rate: 0.85 },
      end_to_end: { rag_latency_pct: 4.2, retrieval_latency_ms: 48 },
    },
    llm_summary: {
      total_calls: 4, successful_calls: 4, failed_calls: 0,
      total_tokens: 4250, prompt_tokens: 3100, completion_tokens: 1150,
      total_cost_usd: 0.01925, total_latency_ms: 8400,
      by_agent: {
        triage:    { calls: 1, total_tokens: 850,  prompt_tokens: 720,  completion_tokens: 130, cost_usd: 0.00310, latency_ms: 1200 },
        planner:   { calls: 1, total_tokens: 1400, prompt_tokens: 1050, completion_tokens: 350, cost_usd: 0.00613, latency_ms: 2100 },
        solver:    { calls: 1, total_tokens: 1600, prompt_tokens: 1080, completion_tokens: 520, cost_usd: 0.00790, latency_ms: 3800 },
        validator: { calls: 1, total_tokens: 400,  prompt_tokens: 250,  completion_tokens: 150, cost_usd: 0.00213, latency_ms: 1300 },
      },
    },
    judge: {
      factuality_score: 0.95, completeness_score: 0.90, confidence_calibration: 0.88,
      hallucination_flag: false, issues: [], overall_score: 0.92, overall_grade: "A",
      verdict_summary: "Triage correctly identified the missing package and affected file from the log.",
    },
  },
  {
    event_id: "evt-demo-repo-456-20260609T110000Z",
    repo: "org/data-pipeline",
    status: "denied",
    triage: { failure_type: "test_failure", confidence: 0.71, summary: "AssertionError in test_transform.py line 88" },
    policy: { decision: "deny" },
    rag_evaluation: {
      grade: { letter: "C", score: 0.58, retrieval_score: 0.62, context_score: 0.55, generation_score: 0.57 },
      retrieval: { hit_rate: 1.0, mean_similarity: 0.61, mrr: 0.5, recall_at_k: 0.66, result_count: 2 },
      context_quality: { failure_type_match_rate: 0.5, context_diversity: 2, duplicate_ratio: 0 },
      generation_impact: { rag_value_score: 0.57, type_alignment: false, grounding_rate: 0.4 },
      end_to_end: { rag_latency_pct: 6.8, retrieval_latency_ms: 72 },
    },
    llm_summary: {
      total_calls: 2, successful_calls: 2, failed_calls: 0,
      total_tokens: 1800, prompt_tokens: 1400, completion_tokens: 400,
      total_cost_usd: 0.00750, total_latency_ms: 3200,
      by_agent: {
        triage:  { calls: 1, total_tokens: 750,  prompt_tokens: 620,  completion_tokens: 130, cost_usd: 0.00285, latency_ms: 1100 },
        planner: { calls: 1, total_tokens: 1050, prompt_tokens: 780,  completion_tokens: 270, cost_usd: 0.00465, latency_ms: 2100 },
      },
    },
    judge: {
      factuality_score: 0.65, completeness_score: 0.55, confidence_calibration: 0.50,
      hallucination_flag: true, issues: ["Triage cited line 88 but log shows line 92"], overall_score: 0.58, overall_grade: "C",
      verdict_summary: "Triage missed the actual line number; minor hallucination flagged.",
    },
  },
  {
    event_id: "evt-demo-repo-789-20260609T100000Z",
    repo: "org/auth-service",
    status: "completed",
    triage: { failure_type: "import_error", confidence: 0.88, summary: "ImportError: cannot import 'verify_token' from 'utils'" },
    policy: { decision: "allow" },
    pr: { url: "https://github.com/org/auth-service/pull/17", number: 17 },
    rag_evaluation: {
      grade: { letter: "B", score: 0.78, retrieval_score: 0.82, context_score: 0.75, generation_score: 0.77 },
      retrieval: { hit_rate: 1.0, mean_similarity: 0.79, mrr: 1.0, recall_at_k: 1.0, result_count: 3 },
      context_quality: { failure_type_match_rate: 0.66, context_diversity: 2, duplicate_ratio: 0 },
      generation_impact: { rag_value_score: 0.77, type_alignment: true, grounding_rate: 0.7 },
      end_to_end: { rag_latency_pct: 5.1, retrieval_latency_ms: 53 },
    },
    llm_summary: {
      total_calls: 4, successful_calls: 4, failed_calls: 0,
      total_tokens: 3680, prompt_tokens: 2710, completion_tokens: 970,
      total_cost_usd: 0.01648, total_latency_ms: 7100,
      by_agent: {
        triage:    { calls: 1, total_tokens: 800,  prompt_tokens: 690,  completion_tokens: 110, cost_usd: 0.00283, latency_ms: 1150 },
        planner:   { calls: 1, total_tokens: 1280, prompt_tokens: 950,  completion_tokens: 330, cost_usd: 0.00568, latency_ms: 2050 },
        solver:    { calls: 1, total_tokens: 1280, prompt_tokens: 870,  completion_tokens: 410, cost_usd: 0.00627, latency_ms: 2700 },
        validator: { calls: 1, total_tokens: 320,  prompt_tokens: 200,  completion_tokens: 120, cost_usd: 0.00170, latency_ms: 1200 },
      },
    },
    judge: {
      factuality_score: 0.85, completeness_score: 0.80, confidence_calibration: 0.78,
      hallucination_flag: false, issues: [], overall_score: 0.82, overall_grade: "B",
      verdict_summary: "Triage correctly identified the missing symbol and source module.",
    },
  },
];
