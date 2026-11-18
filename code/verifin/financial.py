"""Explicit standard taxonomy aliases and safe cumulative-flow boundaries.

Aliases aid discovery; the returned exact tag/definition remains authoritative.
Unknown concepts remain queryable explicitly but are not assumed additive.
"""
import re
from datetime import timedelta
from .data import DataError, parse_day

METRICS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "operating_expenses": ["OperatingExpenses"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "weighted_shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "weighted_shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "stockholders_equity": ["StockholdersEquity"],
    "inventory": ["InventoryNet"],
    "accounts_receivable": ["AccountsReceivableNetCurrent"],
}
INSTANT_METRICS = {"assets", "current_assets", "liabilities", "current_liabilities", "cash",
                   "stockholders_equity", "inventory", "accounts_receivable"}
NONADDITIVE_METRICS = INSTANT_METRICS | {"eps_basic", "eps_diluted", "weighted_shares_basic", "weighted_shares_diluted"}
ADDITIVE_CONCEPTS = frozenset("us-gaap:" + concept for alias, tags in METRICS.items()
                             if alias not in NONADDITIVE_METRICS for concept in tags)

COMMON_INCOME = "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic"
VALUATION_ALLOWANCE_EXPENSE = "us-gaap:IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance"
TAX_SCENARIO = "exclude_tax_expense_item"
TAX_SCENARIO_BASIS = "single-item scenario, other allocations held fixed"

CALCULATIONS = ["ratio", "margin", "growth", "difference", "percentage_point_change", "derive_quarter", "price_return", TAX_SCENARIO]
_OPERAND_COUNTS = {**dict.fromkeys(CALCULATIONS, 2), "sum":None, "midpoint":2,
                   "statement_quarter_residual":2, "sales_calendar_growth_gap":4}


def check_operand_shape(operation, ids):
    if not isinstance(operation, str) or operation not in _OPERAND_COUNTS:
        raise DataError("Unknown calculation operation; cannot independently reproduce its value.")
    required = _OPERAND_COUNTS[operation]
    if not ids or len(set(ids)) != len(ids):
        raise DataError("Calculation needs distinct, nonempty operand IDs.")
    if required is not None and len(ids) != required:
        raise DataError(f"{operation} requires exactly {required} operands.")


def tax_scenario_dimensions(earnings, expense):
    """Single-item sensitivity on common earnings using an expense-signed tax reconciliation item."""
    if (earnings.get("concept") != COMMON_INCOME or expense.get("concept") != VALUATION_ALLOWANCE_EXPENSE
            or any(row.get("kind") != "financial_fact" or row.get("basis") != "US-GAAP"
                   or row.get("scale", 1) != 1 or not row.get("currency") or row.get("unit") != row["currency"]
                   for row in (earnings, expense))):
        raise DataError("Use reported basic common earnings and a monetary valuation-allowance change signed as tax expense, in that order.")
    fields = ("entity", "business_scope", "period_start", "period_end", "unit", "currency")
    if any(not earnings.get(k) or earnings[k] != expense.get(k) for k in fields):
        raise DataError("The common earnings and tax-expense effect must have identical issuer, scope, currency and duration.")
    return {**{k:earnings[k] for k in fields}, "basis":TAX_SCENARIO_BASIS,
            "metric":"common_income_excluding_valuation_allowance_change"}


def check_tax_scenario_comparison(scenario, reported):
    if (scenario.get("basis") != TAX_SCENARIO_BASIS or reported.get("basis") != "US-GAAP"
            or reported.get("kind") != "financial_fact" or reported.get("concept") != COMMON_INCOME
            or any(not scenario.get(k) or scenario[k] != reported.get(k)
                   for k in ("entity", "business_scope", "unit", "currency"))):
        raise DataError("Compare this single-item common-income scenario with the same issuer's reported basic common earnings.")
    start, end = (parse_day(scenario.get(k)) for k in ("period_start", "period_end"))
    prior_start, prior_end = (parse_day(reported.get(k)) for k in ("period_start", "period_end"))
    if (prior_start > prior_end or start > end or start <= prior_end
            or abs(((end - start) - (prior_end - prior_start)).days) > 7):
        raise DataError("The scenario comparison requires an earlier, nonoverlapping reported period of comparable duration.")


def chart_comparison(a, b, operation):
    if (a.get("cell_type") != "chart_inventory_cell" or b.get("cell_type") != a["cell_type"]
            or not all(a.get(k) and a[k] == b.get(k) for k in ("entity", "figure_id", "unit", "metric"))
            or operation not in ("difference", "growth")
            or not all(re.fullmatch(r"[0-9]{4}Q[1-4]", str(row.get("chart_period"))) for row in (a, b))
            or a["chart_period"] <= b["chart_period"]):
        raise DataError("Compare a later and an earlier printed quarter of the same inventory series.")
    return b["chart_period"], a["chart_period"]


def duration_class(start, end):
    """Length description only; never an asserted fiscal-quarter/YTD label."""
    if start is None:
        return "instant"
    days = (parse_day(end) - parse_day(start)).days + 1
    if days <= 0:
        raise DataError("Invalid financial duration.")
    for low, high, label in ((70, 105, "quarter_length"), (150, 210, "half_year_length"),
                             (240, 300, "nine_month_length"), (330, 380, "annual_length")):
        if low <= days <= high:
            return label
    return "other_duration"


def quarter_from_cumulative(a, b):
    """Validate additive H1−Q1, nine-month−H1, or FY−nine-month flows."""
    if a.get("cell_type") == "revenue_bridge_cell" or b.get("cell_type") == "revenue_bridge_cell":
        fields = ("entity", "metric", "basis", "business_scope", "currency", "unit",
                  "factor_categories", "comparison_start", "period_start")
        if not (a.get("cell_type") == b.get("cell_type") == "revenue_bridge_cell"
                and a.get("concept") == b.get("concept") == "issuer:revenue_change_contribution"
                and all(a.get(k) and a[k] == b.get(k) for k in fields)):
            raise DataError("Revenue bridge components must use identical factors, population, and comparison basis.")
        for row in (a, b):
            current = parse_day(row.get("period_end"))
            prior = parse_day(row.get("comparison_end"))
            if (current.year - prior.year, current.month) != (1, prior.month):
                raise DataError("Bridge operands must compare the corresponding prior-year interval.")
    elif a.get("concept") not in ADDITIVE_CONCEPTS or b.get("concept") != a.get("concept"):
        raise DataError("Quarter derivation requires a known additive flow concept; EPS, weighted-average shares, ratios, balances and unknown concepts cannot be subtracted as quarters.")
    return residual_quarter(a, b)


def residual_quarter(a, b, *, allow_same_end=False):
    """Date arithmetic for additive flows. Return the actual uncovered interval."""
    sa = parse_day(a.get("period_start"))
    sb = parse_day(b.get("period_start"))
    ea = parse_day(a.get("period_end"))
    eb = parse_day(b.get("period_end"))
    current, prior = (ea - sa).days + 1, (eb - sb).days + 1
    if sa == sb and ea > eb:
        start, end = eb + timedelta(days=1), ea
    elif allow_same_end and ea == eb and sa < sb:
        start, end = sa, sb - timedelta(days=1)
    else:
        raise DataError("Quarter subtraction needs nested durations sharing the correct fiscal boundary.")
    residual = (end - start).days + 1
    pairs = ((150, 210, 70, 105), (240, 300, 150, 210), (330, 380, 240, 300))
    if not 70 <= residual <= 105 or not any(cl <= current <= ch and pl <= prior <= ph for cl, ch, pl, ph in pairs):
        raise DataError("Quarter derivation requires consecutive cumulative periods with the same start: half-year minus Q1, nine-month minus half-year, or full-year minus nine-month YTD.")
    return {"current_days": current, "prior_days": prior, "quarter_days": residual,
            "period_start":start.isoformat(), "period_end":end.isoformat()}
