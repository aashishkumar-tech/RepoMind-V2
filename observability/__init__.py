"""
observability — Observability + Kill Switch

Provides:
  - Prometheus metrics via Pushgateway (observability.metrics)
  - Global kill switch via AWS SSM Parameter Store (observability.killswitch)

COMMUNICATION:
─────────────
Worker (worker) calls:
  - is_kill_switch_enabled() at pipeline start
  - push_metrics() at pipeline end
Every step can emit metrics via the shared registry.
"""
