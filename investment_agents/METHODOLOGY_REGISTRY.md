# Methodology Registry

This registry distinguishes source-backed methods from project interpretations
and engineering controls. It is not a performance claim or investment advice.

| Rule | Category | Source/reference | Scope and limitations | Qualification/ranking/execution impact |
|---|---|---|---|---|
| Piotroski nine-signal framework and 0-9 sum | SOURCE_BACKED framework / adapted implementation | Piotroski (2000), DOI 10.2307/2672906 | The original study used Compustat data for high book-to-market firms. The yfinance row mapping and the turnover denominator noted below are adaptations. Incomplete inputs remain partial. | Diagnostic only; no direct trade |
| F_DELTA_TURN = change in sales / average total assets | SOURCE_BACKED | Piotroski (2000) | Formal implementation version `piotroski-2000-average-assets-v2` | Piotroski component and Pareto eligibility/value |
| Legacy F_DELTA_TURN = sales / beginning assets | PROJECT_HEURISTIC / legacy comparison only | Former project choice | Named legacy API returns no official score and is not rankable | No formal qualification/ranking use |
| Normalized PR = PE_TTM / ROE_pct | PROJECT_HEURISTIC | Project-defined valuation factor | ROE is percentage-number units; not an official Buffett/Munger rule | Context and Pareto dimension |
| PR PROJECT_REFERENCE_BAND 0.4-0.6 | PROJECT_HEURISTIC | Approved project reference band | Descriptive only; not a buy line or qualification threshold | No qualification/ranking/execution impact |
| PR_corrected = N x PR | EXPERIMENTAL | Project-defined payout adjustment | Does not override raw PR; no qualification or ranking use | No execution impact |
| PB-based PR_cycle | PROJECT_HEURISTIC | Project-defined formula | Requires explicit 3Y/5Y ROE window and current PB; PE/ROE is only a reference | Context only |
| Historical ROE = annual net income / average equity | ACCOUNTING_DERIVED | Annual statement fields | Current snapshot fields are prohibited; non-positive average equity is unavailable | PR_cycle input only when a real window exists |
| FinancialDiagnostics ratios/warnings | ACCOUNTING_DERIVED | Formula-derived from supplied statements | Missing data remains missing; warnings are factual labels | No score or rejection |
| Critical-data qualification | PROJECT_HEURISTIC | Project design | Distinguishes insufficient data from quality warnings | Qualification status only |
| Current-loss strategy exclusion | PROJECT_HEURISTIC | Declared profitable-current-value-analysis scope | Not a price forecast or universal investment rule | Only explicit quality-based exclusion |
| Pareto dimensions: complete Piotroski and normalized PR | PROJECT_HEURISTIC | Project design | Exactly two unweighted dimensions; no hidden tie-break ranking | Research priority only |
| Research Queue lanes | ENGINEERING_CONTROL | Project contract | Ranked and qualified-unranked remain separate | Preserves candidates |
| Budget preflight and no silent truncation | ENGINEERING_CONTROL | Project contract | Call limits fail closed; token overshoot remains a documented limitation | Execution scheduling only |
| Buffett/Munger personas | SOURCE_BACKED persona attribution / adapted runtime | virattt/ai-hedge-fund attribution | Local runtime is not the full upstream runtime; output is opinion | No trade output |
| Committee synthesis | PROJECT_HEURISTIC / ENGINEERING_CONTROL | Project design | Preserves disagreement; no voting score | Research synthesis only |
| Event materiality | PROJECT_HEURISTIC / MODEL_INTERPRETATION | Project rules | Metadata/count alone must not establish materiality; current headline handling needs a methodology decision | No qualification or trading impact |
| Thesis impact | PROJECT_HEURISTIC / MODEL_INTERPRETATION | Approved MDR-05 rule | No defined explicit baseline comparison returns INSUFFICIENT_EVIDENCE; absence of failures never means no change | No trade output |
| Source hierarchy | ENGINEERING_CONTROL | Primary corporate source > secondary news claim > macro context > social sentiment | Provenance categories, not numeric trust weights | Evidence provenance only |
| Macro context | SOURCE_BACKED data / ENGINEERING_CONTROL use | FRED structured observations | Current-vintage context, not a company event or PIT series | No automatic impact |
| Sentiment handling | ENGINEERING_CONTROL | Provider capability and rate-limit status | Unavailable or rate-limited is not neutral or negative sentiment | No impact |
| EnhancedResearchPackage provenance layers | ENGINEERING_CONTROL | Project integration contract | Interpretation must never become a primary fact | Prompt/data boundary |
