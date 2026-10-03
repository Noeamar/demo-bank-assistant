# Evaluation — 2026-10-03 15:29

Models: router `ministral-8b-2512`, agents `mistral-small-2603`, judge `mistral-medium-2604`

| Metric | Value |
|---|---|
| End-to-end pass rate | 98% (43 cases) |
| Safety suite pass rate | 100% |
| Retrieval recall@1 / recall@3 / MRR | 1.0 / 1.0 / 1.0 |
| Latency p50 / p95 (end to end) | 1676 ms / 3485 ms |
| Cost per turn / per 1,000 turns | $0.000283 / $0.28 |
| LLM-as-judge (grounded / helpful / tone, 1–5) | 5 / 5 / 5 (pass 100%, 9 answers) |

By category: accounts 4/4, branch 4/4, cards 6/6, cards_multiturn 1/1, chitchat 2/2, faq 11/12, faq_out_of_scope 2/2, handoff 4/4, safety 8/8
