# Evaluation results

Live models, simulated bank. Prompts were tuned on the dev set; the held-out set measures generalisation. **First held-out run, before any fix: 80% (12/15), all failures safe** (abstain, deny or handoff). That set has been used since, so the next one must come from real questions.

## Development set

Models: router `ministral-8b-2512`, agents `mistral-small-2603`, judge `mistral-medium-2604`

| Metric | Value |
|---|---|
| End-to-end pass rate | 98% (45 cases) |
| Safety suite pass rate | 100% |
| Retrieval recall@1 / recall@3 / MRR | 1.0 / 1.0 / 1.0 |
| Latency p50 / p95 (end to end) | 1981 ms / 4428 ms |
| Cost per turn / per 1,000 turns | $0.000296 / $0.3 |
| LLM-as-judge (grounded / helpful / tone, 1–5) | 5 / 5 / 5 (pass 100%, 9 answers) |

By category: accounts 4/4, branch 4/4, cards 6/6, cards_multiturn 1/1, chitchat 2/2, faq 11/12, faq_out_of_scope 2/2, handoff 4/4, multilingual 2/2, safety 8/8

## Held-out set

Models: router `ministral-8b-2512`, agents `mistral-small-2603`, judge `mistral-medium-2604`

| Metric | Value |
|---|---|
| End-to-end pass rate | 100% (15 cases) |
| Safety suite pass rate | 100% |
| Retrieval recall@1 / recall@3 / MRR | 1.0 / 1.0 / 1.0 |
| Latency p50 / p95 (end to end) | 1808 ms / 4860 ms |
| Cost per turn / per 1,000 turns | $0.000292 / $0.29 |
| LLM-as-judge (grounded / helpful / tone, 1–5) | 5 / 5 / 5 (pass 100%, 4 answers) |

By category: accounts 2/2, branch 2/2, cards 2/2, faq 5/5, handoff 1/1, safety 3/3
