# Candidate Qualification (Step 5)

Qualification consumes existing data, PR, FinancialDiagnostics, and Piotroski
results. It does not recalculate them and never emits a trade instruction,
ranking, or position sizing. A future scanner may legally return
`NO_QUALIFIED_CANDIDATE` with count `0`; there is no best-available or forced
Top-N fallback.

The only first-version exclusion is a clearly negative current net income,
which is outside the declared profitable long-term equity candidate scope. It
does not predict that the stock will fall. Net debt, negative FCF, basis
warnings, the PR project reference band, and partial Piotroski data remain warnings or data
diagnostic incompleteness, not automatic rejection. Critical qualification
data is limited to ticker/as-of, current net income, revenue, and current
equity/debt/cash context. Piotroski completeness, EQOFFER, and
CURRENT_ANALYSIS filing/availability timestamps are optional diagnostic
completeness and are reported explicitly in `optional_diagnostic_missing`.
