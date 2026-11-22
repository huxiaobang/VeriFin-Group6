"""Opt-in project-authored research methods. Contains no company answers or proprietary text."""
from hashlib import sha256
import json

ROLE_GUIDANCE = {
    "planner": (
        "First identify what the user wants compared: issuer or segment, period, actual or forecast, "
        "reported or adjusted basis, and publication cutoff. On follow-ups retain unchanged fields; "
        "reopen the ones the user changed and keep previous answers as historical. For a peer, get each "
        "issuer's own same-method evidence before comparing; seek both sides and comparative periods. "
        "For an earnings surprise retrieve the previous guidance before the release, then the observed "
        "actual; street consensus is a different, timestamped source. For reported quarterly revenue versus "
        "the prior issuer guidance midpoint, select implied-period-guidance and pass "
        "comparison=direct_quarter_revenue_guidance and the requested fiscal_quarter to analyze_disclosures. "
        "For profit-versus-cash or sales "
        "drivers, collect the reconciliation and notes, not just the top-line change. Stop requesting "
        "new sources once the facts and remaining disclosure gaps are clear."
    ),
    "researcher": (
        "Lead with a brief answer in the user's language. Distinguish what the company reports, what it "
        "attributes, and what can actually be calculated. A change of accounting profit cannot be traced "
        "one-for-one into cash: align parent/consolidated, cash vs accrual and pre/post-tax bases. For "
        "a payment, compare the same note's cumulative cash totals and state whether several programs "
        "are pooled. If a reconciliation combines acquisitions, mix or rounding, do not allocate the "
        "residual to one driver. When comparing peers specify measure level vs change, numerator and "
        "denominator for each, duration, units, and material definition differences. For 'beat', name "
        "whose original forecast, compare midpoint and bounds separately, and qualify absent consensus. "
        "A change in market price alone does not identify the cause. Split independent assertions."
    ),
    "critic": (
        "Test each answer against the comparison it actually promises. Is an actual being matched to an "
        "estimate, a level to a change, another period, a different population or tax basis? A number "
        "within a forecast interval cannot beat its top even if it beats midpoint. Do not infer the cash "
        "paid for a single event from pooled charges or multi-event cash totals. Check the denominator "
        "when the numerator is adjusted and the sign when a company reconciles reported to organic. "
        "For a change of scope request new inputs and recomputation of the affected claim; keep correct "
        "unrelated claims. Flag unmeasured attribution or causality, even with a plausible source."
    ),
}
PACK_NAME='financial-workflows-v1'
PACK_V2='financial-workflows-v2'
PACK_NAMES=(PACK_NAME, PACK_V2)
# Original workflow instructions, without examples, company names or answers.
# Each extension applies only if the requested comparison needs it.
OPERATING_DRIVERS = {
    "planner": (
        "For an open-ended quarterly overview, choose a few material facts about that quarter and a "
        "prior-period comparator. Do not make a full-year review or outlook mandatory unless the user asks, "
        "or it is necessary to interpret the quarter. For operating drivers, identify who measured what: manufacturer shipments, dealer sales, "
        "end-user purchases, inventory held by a specific party, or signed orders versus recognized revenue. "
        "Look for the issuer's operating tables, definitions and a roll-forward with both endpoints. "
        "For comparable units distinguish existing and newly opened cohorts from total sales. "
        "For lodging check whether occupancy, rate and room revenue refer to the same properties. "
        "If the user moves the cutoff, recheck when each source became public before reusing its numbers."
    ),
    "researcher": (
        "For an open overview, prioritize a short set of decision-relevant claims that covers the "
        "current question; preserve the exact citations without repeating the same fact. A stock at a date, a within-quarter change, and a year-on-year change in stocks describe "
        "different things. Report sell-in and sell-through separately with their units and coverage. "
        "Do not turn their difference into a dealer inventory count without a matching perimeter and "
        "the other movements. Distinguish order intake, outstanding obligations, options and sales. "
        "Use cumulative-period subtraction only for like-for-like flow amounts. Distinguish all stores, "
        "comparable stores and openings by cohort. A price/traffic or room-rate/occupancy bridge must "
        "match the metric definition; percentage changes compound, and levels can change by percentage points."
    ),
    "critic": (
        "Flag a causal inventory, cancellation, new-store or share claim only when it goes beyond the "
        "actual reconciliation or compared populations. An issuer's own units sold are not a market "
        "share denominator. Distinct disclosed measures can both be correct. Accept supported numbers "
        "and a clearly bounded unknown together; do not order another search for the same undisclosed component."
    ),
}


def configure_methods(model, name):
    if name is None:
        return model
    if name not in PACK_NAMES:
        raise ValueError('Unknown research method pack')
    model.role_guidance = dict(ROLE_GUIDANCE)
    if name == PACK_V2:
        model.role_guidance = {role:text+" "+OPERATING_DRIVERS[role] for role,text in model.role_guidance.items()}
    model.method_pack = name
    model.method_pack_sha256 = sha256(json.dumps(model.role_guidance,sort_keys=True).encode()).hexdigest()
    return model
