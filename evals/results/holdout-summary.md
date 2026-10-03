# Evaluation — 2026-10-03 15:30

Models: router `ministral-8b-2512`, agents `mistral-small-2603`, judge `mistral-medium-2604`

| Metric | Value |
|---|---|
| End-to-end pass rate | 100% (15 cases) |
| Safety suite pass rate | 100% |
| Retrieval recall@1 / recall@3 / MRR | 1.0 / 1.0 / 1.0 |
| Latency p50 / p95 (end to end) | 1756 ms / 6691 ms |
| Cost per turn / per 1,000 turns | $0.000289 / $0.29 |
| LLM-as-judge (grounded / helpful / tone, 1–5) | 5 / 5 / 5 (pass 100%, 4 answers) |

By category: accounts 2/2, branch 2/2, cards 2/2, faq 5/5, handoff 1/1, safety 3/3
