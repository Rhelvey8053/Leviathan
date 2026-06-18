from datetime import datetime, timezone, timedelta

# Time horizon buckets: (label, min_days_inclusive, max_days_exclusive)
BUCKETS = [
    ("INTRADAY",  0,   1),
    ("WEEKLY",    1,   7),
    ("MONTHLY",   7,   30),
    ("QUARTERLY", 30,  90),
    ("LONG",      90,  366),
]

BUCKET_PRIORITY = {b[0]: i for i, b in enumerate(BUCKETS)}

# Watchlist trader set — injected by main.py at startup for priority scoring.
# Markets where a top Polymarket trader holds a position get a boost.
_WATCHLIST_TICKERS: set[str] = set()


def classify_time_horizon(close_time: datetime, now: datetime) -> str:
    """Returns the time bucket label for a market based on days until close."""
    days = (close_time - now).total_seconds() / 86400
    for label, lo, hi in BUCKETS:
        if lo <= days < hi:
            return label
    return "LONG"


def filter_markets(markets: list[dict], config: dict) -> list[dict]:
    """
    Removes markets likely already efficiently priced before any scoring.
    Filters out markets that match ANY of the following:
      1. Volume > max_volume_filter (efficiently priced by crowd)
      2. Volume < bucket min_volume
      3. Open interest < min_open_interest (ghost markets, no real participants)
      4. Title contains efficient market keyword
      5. Closing outside [min_days_to_close, max_days_to_close]
      6. Mid price outside [min_market_price, max_market_price]
    """
    cfg          = config.get("markets", {})
    global_min_vol  = cfg.get("min_volume", 500)
    max_vol         = cfg.get("max_volume_filter", 75000)
    min_days        = cfg.get("min_days_to_close", 0)
    max_days        = cfg.get("max_days_to_close", 180)
    min_price       = cfg.get("min_market_price", 0.05)
    max_price       = cfg.get("max_market_price", 0.95)
    min_oi          = cfg.get("min_open_interest", 0)
    bucket_vol      = cfg.get("bucket_min_volume", {})
    keywords        = [k.lower() for k in cfg.get("efficient_market_keywords", [])]

    now       = datetime.now(timezone.utc)
    min_close = now + timedelta(days=min_days)
    max_close = now + timedelta(days=max_days)

    filtered = []
    for m in markets:
        volume = float(m.get("volume_fp") or m.get("volume") or 0)

        if volume > max_vol:
            continue

        # Open interest floor — exclude ghost markets with no active participants
        if min_oi > 0:
            oi = float(m.get("open_interest_fp") or m.get("open_interest") or 0)
            if oi < min_oi:
                continue

        # Price bounds — exclude near-certain and tail-probability contracts
        yes_bid = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
        yes_ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
        if yes_bid > 0 and yes_ask > 0:
            # Two-sided market — use true mid
            mid = (yes_bid + yes_ask) / 2
        else:
            # One-sided or empty book (often a settled leg with a stale ask) —
            # use last traded price as the best available price estimate
            last_p = float(m.get("last_price_dollars") or 0)
            mid = last_p if last_p > 0 else None
        if mid is not None and not (min_price <= mid <= max_price):
            continue

        # Efficient market keyword check
        title = (m.get("title") or "").lower()
        if any(kw in title for kw in keywords):
            continue

        # Close time bounds
        close_time_str = m.get("close_time") or m.get("expiration_time")
        if not close_time_str:
            continue
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if close_time < min_close or close_time > max_close:
            continue

        # Per-bucket volume minimum (shorter horizons tolerate lower volume)
        bucket    = classify_time_horizon(close_time, now)
        min_vol   = bucket_vol.get(bucket, global_min_vol)
        if volume < min_vol:
            continue

        m["time_horizon"] = bucket
        filtered.append(m)

    return filtered


def dedup_by_event(markets: list[dict]) -> list[dict]:
    """
    When multiple markets share the same event_ticker, keep only the one with
    the highest volume. Prevents the same underlying event (e.g. 10 Prison Break
    expiry tickers) from consuming the entire Claude scoring budget.

    For scored markets (after score_markets()), prefer dedup_by_event_scored()
    which uses realizable edge as the selection criterion instead of volume.
    """
    by_event: dict[str, dict] = {}
    no_event: list[dict]      = []

    for m in markets:
        ev = m.get("event_ticker", "").strip()
        if not ev:
            no_event.append(m)
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        existing_vol = float(
            by_event[ev].get("volume_fp") or by_event[ev].get("volume") or 0
        ) if ev in by_event else -1
        if vol > existing_vol:
            by_event[ev] = m

    return list(by_event.values()) + no_event


def _event_priority(m: dict) -> tuple:
    """
    Priority key for dedup_by_event_scored(): higher tuple = better market.
    1. watchlist_signal (smart money confirmation wins outright)
    2. net_edge (realizable edge after spread — None treated as -inf)
    3. raw_edge (theoretical edge fallback)
    4. volume (liquidity tiebreaker)
    """
    return (
        1 if m.get("watchlist_signal") else 0,
        m.get("net_edge") if m.get("net_edge") is not None else -1.0,
        m.get("raw_edge") if m.get("raw_edge") is not None else 0.0,
        float(m.get("volume_fp") or m.get("volume") or 0),
    )


def dedup_by_event_scored(markets: list[dict]) -> list[dict]:
    """
    Post-scoring event dedup: keeps the market with the best signal per event.

    Selection priority (see _event_priority):
    1. Watchlist signal (smart money confirmation)
    2. Net-of-spread edge (realizable edge after bid-ask cost)
    3. Raw edge
    4. Volume (fallback)

    This is strictly better than the pre-scoring volume-only dedup when
    scored fields are available.
    """
    by_event: dict[str, dict] = {}
    no_event: list[dict]      = []

    for m in markets:
        ev = m.get("event_ticker", "").strip()
        if not ev:
            no_event.append(m)
            continue
        if ev not in by_event or _event_priority(m) > _event_priority(by_event[ev]):
            by_event[ev] = m

    return list(by_event.values()) + no_event


def estimate_base_rate(market: dict) -> float | None:
    """
    Simple heuristic pass before calling Claude (saves tokens).
    Returns a float 0.0–1.0 if a known signal applies, else None.
    scorer.py handles None markets with the full Claude call.
    """
    title = (market.get("title") or "").lower()

    # Binary yes/no events with known rough base rates.
    # Order matters — more specific patterns should come first.
    heuristics = [
        # Sports — outcomes for individual games lean slight favourite
        (["win the world series", "win world series"], 0.50),
        (["win the championship", "win the nba", "win the nfl", "win the cup",
          "win the world cup", "win the fifa", "world cup winner",
          "world series winner", "win the champions league",
          "champions league winner", "stanley cup"], 0.50),
        (["win the super bowl", "super bowl winner"], 0.50),
        (["win the game", "win on", "win their next"], 0.52),
        # Elections — incumbents have modest advantage
        (["win the election", "win election", "wins the election",
          "win the primary", "win the runoff"], 0.52),
        (["win the presidency", "win the white house"], 0.50),
        (["win the senate race", "win the house race", "win the gubernatorial",
          "win the mayoral", "mayor race", "win the governor"], 0.52),
        # Reelection — slight incumbent advantage over a challenger
        (["be reelected", "win reelection", "win re-election",
          "reelected", "re-elected", "secure a second term",
          "win a second term", "second presidential term"], 0.52),
        # Primary challenge — whether a challenge EXISTS (not whether it wins) → ~30%
        # Most incumbents don't face serious primary opponents
        (["primary challenge", "primary challenger", "face a primary",
          "challenge in the primary", "defeated in the primary",
          "lose the primary", "lost the primary",
          "primary opponent"], 0.30),
        # Political withdrawal — candidate dropping out of a race → ~30%
        # "withdraw from the" shorter fragment handles year-insertion ("from the 2024 race")
        (["suspend his campaign", "suspend her campaign",
          "end his campaign", "end her campaign",
          "withdraw his candidacy", "withdraw her candidacy",
          "exit the race", "quit the race",
          "drop out of the", "withdraw from the"], 0.30),
        # Special election — usually called when a seat becomes vacant → ~45%
        # Congress almost always eventually fills vacancies, but timing is uncertain
        (["special election", "special senate election", "special house election",
          "special congressional election", "fill the vacancy",
          "senate vacancy", "house vacancy"], 0.45),
        # Constitutional amendment — requires supermajority in both chambers + 3/4 states → ~5%
        # "abolish the electoral college" is also a de facto amendment question
        (["constitutional amendment", "amend the constitution",
          "constitutional convention", "repeal the 2nd amendment",
          "repeal the amendment", "electoral college amendment",
          "equal rights amendment", "balanced budget amendment",
          "abolish the electoral college", "electoral college be abolished",
          "eliminate the electoral college"], 0.05),
        # Snap election / early election — triggered by dissolution of parliament (~25%)
        (["snap election", "early election", "early general election",
          "call for early elections", "dissolve parliament",
          "call a general election"], 0.25),
        # Political withdrawal addendum — "not seek" phrasing (no year-injection risk)
        (["not seek a second term", "not seek reelection", "not seek re-election",
          "will not run for", "choose not to run", "choosing not to run",
          "decided not to run", "decide not to run",
          "not stand for reelection", "not stand for re-election"], 0.30),
        # Ballot disqualification — courts rarely disqualify candidates → ~20%
        # "disqualified from the" handles year insertion ("from the 2024 ballot")
        (["ballot disqualification", "ineligible for the ballot",
          "kicked off the ballot", "barred from the ballot",
          "disqualified from the", "removed from the ballot",
          "disqualified from running", "disqualified from appearing"], 0.20),
        # Student loan forgiveness / debt cancellation — executive or legislative → ~30%
        (["student loan forgiveness", "student loan cancellation",
          "student debt cancellation", "student loan relief",
          "cancel student debt", "student debt forgiveness",
          "student loan discharge"], 0.30),
        # Healthcare reform — comprehensive overhaul is historically rare → ~20%
        # Placed BEFORE generic "pass the senate"/"become law" to take priority
        (["healthcare reform", "health care reform", "healthcare system be reformed",
          "healthcare system reformed", "health care system",
          "universal healthcare", "universal health care",
          "medicare for all", "medicaid expansion",
          "affordable care act", "health insurance reform",
          "single payer", "public option"], 0.20),
        # Minimum wage legislation — requires Congressional action; historically slow → ~25%
        # Placed BEFORE generic legislative block ("pass the senate" etc.) because title
        # typically says "raise the minimum wage" rather than "pass the bill"
        (["minimum wage", "raise the minimum wage", "increase the minimum wage",
          "minimum wage increase", "minimum wage hike", "minimum wage legislation",
          "federal minimum wage", "minimum wage to $", "minimum wage bill"], 0.25),
        # Congressional spending — continuing resolutions / omnibus bills (must come before
        # generic "signed into law" because "omnibus bill" is a more specific match)
        (["continuing resolution", "omnibus bill", "appropriations bill",
          "government funding bill", "spending bill",
          "federal budget", "budget resolution", "budget deal", "budget agreement",
          "pass the budget", "budget bill", "budget deadline"], 0.40),
        # Legislative — most Kalshi bills have some momentum; passage ~35%
        # Includes both "pass the senate" AND "senate pass" word orderings
        (["pass the senate", "pass the house", "pass congress",
          "pass in the senate", "pass in the house",
          "pass into law", "signed into law", "sign into law",
          "pass the bill", "passes the bill", "pass legislation",
          "become law", "enacted into law",
          "senate pass", "house pass", "senate approve", "house approve",
          "senate vote on", "house vote on"], 0.35),
        (["veto", "presidential veto", "veto the bill",
          "pocket veto"], 0.20),
        # National emergency declaration — executive action used for crises; any single
        # trigger is uncertain (~25%); placed before executive_order (0.45)
        (["national emergency", "declare a national emergency",
          "declare an emergency", "emergency declaration",
          "invoke emergency powers", "state of emergency",
          "invoke the national emergencies act",
          "invokes emergency powers", "emergency powers act"], 0.25),
        # Executive / political appointments
        (["executive order", "sign an executive order",
          "issue an executive order"], 0.45),
        (["senate confirmation", "confirmed by the senate",
          "cabinet nomination", "confirmed as secretary",
          "confirmed as director", "confirmed as ambassador"], 0.55),
        # Cabinet departure — "will any member of X's cabinet leave" (aggregate, high turnover)
        (["member of trump's cabinet", "trump cabinet member",
          "member of the cabinet leave", "cabinet member leave",
          "leave the cabinet", "depart from the cabinet"], 0.65),
        (["resign", "step down", "stepping down",
          "resigns from", "resignation"], 0.20),
        # Athlete / celebrity retirement — players often defer decision; markets are uncertain → ~30%
        # Placed after resign (0.20) as retirement is distinct from workplace resignation
        (["announce retirement", "announce his retirement", "announce her retirement",
          "retire from the nba", "retire from the nfl", "retire from the mlb",
          "retire from the nhl", "retire from pro", "retire from professional",
          "retire from football", "retire from basketball", "retire from baseball",
          "decide to retire", "officially retire", "retirement before",
          "retirement announcement", "announce they will retire"], 0.30),
        (["pardon", "presidential pardon", "commute the sentence"], 0.35),
        # Congressional control — election-cycle markets near 50/50
        (["control the senate", "senate majority", "senate control",
          "majority in the senate", "control of the senate",
          "control the house", "house majority", "house control",
          "majority in the house", "control of the house",
          "congressional majority", "take control of the senate",
          "take control of the house", "flip the senate", "flip the house",
          "senate seat", "senate race 20"], 0.50),
        # Sanctions — check "lift/remove" first (more specific) before generic "impose"
        (["lift sanctions", "remove sanctions",
          "ease sanctions", "waive sanctions"], 0.20),
        (["impose sanctions", "new sanctions",
          "sanctions on", "sanctions against"], 0.45),
        # Nuclear power plant accident — extremely rare; distinct from weapons programs → 5%
        # Placed BEFORE nuclear weapons + nuclear deal blocks to catch "nuclear plant" specifically
        (["nuclear power plant", "nuclear plant accident", "nuclear reactor",
          "nuclear meltdown", "nuclear accident", "nuclear incident",
          "chernobyl", "fukushima", "reactor failure", "reactor meltdown"], 0.05),
        # Nuclear weapons development / acquisition — extremely rare in any given window → 5%
        # Placed BEFORE "nuclear deal" (0.20) to prevent misclassifying weapons-capability titles
        (["develop a nuclear weapon", "develop nuclear weapons",
          "acquire nuclear weapons", "acquire nuclear capability",
          "become a nuclear power", "nuclear weapons program",
          "achieve nuclear capability", "nuclear armed",
          "nuclear warhead", "nuclear device"], 0.05),
        (["nuclear deal", "nuclear agreement", "nuclear accord",
          "nuclear treaty", "npt", "iaea agreement"], 0.20),
        (["peace deal", "ceasefire", "peace agreement", "armistice"], 0.25),
        (["join nato", "nato membership", "nato expansion",
          "nato accession"], 0.35),
        # Rejoin an international agreement — countries rarely rejoin after withdrawal → ~25%
        (["rejoin the paris", "rejoin paris", "rejoin the un",
          "rejoin the who", "rejoin the tpp",
          "return to the agreement", "re-enter the agreement",
          "rejoin the deal", "return to the accord",
          "re-enter the paris", "rejoin the accord"], 0.25),
        (["join the eu", "eu membership", "eu accession",
          "european union membership"], 0.25),
        # Common/shared currency adoption — extremely rare monetary policy change → ~10%
        (["common currency", "shared currency", "unified currency",
          "adopt a currency", "currency union", "monetary union",
          "replace the dollar", "replace the euro", "petrodollar"], 0.10),
        # Economic performance comparisons — near 50/50 for "outperform/underperform" questions
        (["outperform", "underperform", "outgrow", "grow faster than",
          "perform better than", "exceed average", "below average growth",
          "economic performance"], 0.50),
        # UN Security Council resolution — China/Russia veto risk keeps rate low
        (["un security council", "united nations security council",
          "security council resolution", "security council vote",
          "pass at the un", "un resolution"], 0.15),
        (["recognize", "diplomatic recognition",
          "normalize relations", "establish relations"], 0.30),
        # Diplomatic meetings / summits — whether the meeting HAPPENS (~40%)
        # Distinct from peace deals (0.25): a summit is scheduled more often than a deal is signed
        (["bilateral summit", "diplomatic summit", "peace summit",
          "summit between", "summit with", "diplomatic meeting",
          "state visit by", "bilateral meeting",
          "meet with xi", "meet with putin", "meet with kim",
          "diplomatic talks between", "diplomatic negotiations",
          "diplomatic engagement"], 0.40),
        # Supreme Court / legal rulings
        (["supreme court", "scotus", "high court ruling",
          "appeals court", "circuit court"], 0.50),
        (["overturns", "upholds", "rules in favor",
          "strikes down", "court ruling", "court decision"], 0.50),
        # Pardon / clemency — president has wide latitude; depends on political climate (~35%)
        (["be pardoned", "receive a pardon", "presidential pardon", "pardon of",
          "receive clemency", "clemency for",
          "commute his sentence", "commute her sentence",
          "commute the sentence", "commute their sentence", "commuted sentence",
          "grant a pardon", "grant clemency"], 0.35),
        # Plea deal — most criminal cases resolve via plea before trial (~45%)
        # Place BEFORE "found guilty" to avoid first-match conflict on "plead guilty"
        (["plead guilty", "plea deal", "plea agreement",
          "enter a guilty plea", "no contest plea",
          "accept a plea", "negotiate a plea"], 0.45),
        # Acquittal / not guilty — for prediction market trials (contested, high-profile)
        # Place BEFORE broad "found guilty" block; "not guilty" is substring-safe vs "found guilty"
        (["be acquitted", "found not guilty", "not guilty verdict",
          "acquitted of", "acquittal", "declared not guilty",
          "ruled not guilty"], 0.35),
        # Criminal / legal — conviction base rates are moderate
        (["convicted", "found guilty", "indicted", "charged with"], 0.40),
        (["impeach", "impeachment", "removed from office"], 0.15),
        # 25th Amendment invocation — historically zero successful non-voluntary uses
        (["25th amendment", "invoke the 25th", "invoked the 25th",
          "section 4 of the 25th", "25th amendment invocation"], 0.05),
        (["lawsuit", "settlement", "settle the lawsuit",
          "class action", "reaches settlement"], 0.40),
        # Face trial — criminal proceeding (~35%); placed BEFORE arrest block
        (["face trial", "stand trial", "go to trial",
          "faces trial", "stands trial", "goes to trial",
          "brought to trial", "criminal trial"], 0.35),
        # Regulatory fines — EU and US regulators fine companies frequently (~40%)
        (["be fined by", "get fined by", "receive a fine",
          "pay a fine", "fined for", "eu fine",
          "regulatory fine", "antitrust fine"], 0.40),
        # Cyberattack / data breach / data leak — significant incidents are unfortunately common (~35%)
        (["cyberattack", "cyber attack", "cyber breach",
          "data breach", "data leak", "data theft",
          "ransomware attack", "hack the infrastructure",
          "critical infrastructure attack", "attack on the grid",
          "government data", "hacked by", "hack into"], 0.35),
        # Arrested / in custody — before convicted/indicted; arrest ≠ conviction
        # "house arrest" avoided by requiring "arrested for/by/in" or standalone phrases
        (["be arrested", "get arrested", "was arrested", "been arrested",
          "arrested for", "arrested by", "taken into custody",
          "arraigned", "in custody"], 0.30),
        # Extradition — formal international legal process, moderately likely when
        # request already filed; much less likely for non-treaty countries
        (["be extradited", "extradited to", "extradition of",
          "extradition request", "extradited from"], 0.35),
        # Congressional testimony / hearings — scheduled hearings usually proceed
        (["testify before congress", "testify before the senate", "testify before the house",
          "testify before a", "congressional testimony", "appear before congress",
          "appear before the senate", "appear before the house",
          "appear before a", "senate hearing", "house hearing", "committee testimony",
          "congressional committee"], 0.50),
        # Fired / dismissed — higher rate than voluntary resignation
        # "fired" excluded (substring of "misfired", "backfired"); use space-bounded forms instead
        (["be fired", "get fired", "was fired", "been fired", "gets fired",
          " fired ", "dismissed", "terminated",
          "be removed", "get removed", "was removed", "been removed",
          "removed from his position", "removed from her position",
          "removed from the position",
          "removed from his role", "removed from her role",
          "removed from the role", "removed from his post",
          "removed from her post", "removed from the post",
          "ousted from", "pushed out of"], 0.25),
        # Corporate layoffs / workforce reduction — major tech/corporate layoffs are common
        (["mass layoffs", "announce layoffs", "planned layoffs",
          "workforce reduction", "headcount reduction", "job cuts",
          "lay off workers", "lay off employees", "cut its workforce",
          "reduce its workforce", "reduce headcount"], 0.35),
        # Government shutdown: CONGRESS AVOIDS a shutdown → ~85% base rate
        # More specific "avoid/avert/end" must come BEFORE general "shutdown" patterns
        (["avoid a shutdown", "avert a shutdown", "prevent a shutdown",
          "avoid the shutdown", "avert the shutdown",
          "end the shutdown", "shutdown ends", "shutdown end",
          "reopen the government", "resolve the shutdown"], 0.85),
        # Government shutdown: a shutdown STARTS / is currently ongoing → ~15%
        (["government shutdown", "partial shutdown", "federal shutdown",
          "shutdown begins", "shutdown starts"], 0.15),
        # Debt ceiling: Congress raises/suspends it — nearly always happens → ~70%
        # More specific resolution terms must come BEFORE generic "debt ceiling"
        (["raise the debt ceiling", "lift the debt ceiling",
          "suspend the debt limit", "debt limit be suspended",
          "debt limit suspended", "increase the debt limit",
          "raise the debt limit", "debt ceiling deal",
          "debt ceiling agreement", "resolve the debt limit"], 0.70),
        # Generic debt ceiling / debt limit — resolution likely but timing uncertain
        (["debt ceiling", "debt limit", "hit the debt ceiling",
          "breach the debt limit", "x-date"], 0.65),
        # Antitrust / regulatory block on mergers
        (["antitrust", "ftc block", "doj block", "block the merger",
          "block the acquisition", "reject the merger",
          "challenge the merger", "challenge the acquisition"], 0.40),
        # North Korea / DPRK — any NK market is likely a provocation/test market (fairly frequent)
        # Placed before generic "nuclear deal" (0.20) to avoid DPRK test markets scoring too low.
        (["north korea missile", "north korea nuclear", "north korea test",
          "north korea launch", "north korea conduct",
          "dprk missile", "dprk nuclear", "dprk test", "dprk launch",
          "dprk conduct", "dprk provoc", " dprk "], 0.40),
        # Weather / natural disasters
        (["will it rain", "chance of rain", "precipitation"], 0.40),
        (["hurricane", "tropical storm", "tropical cyclone",
          "category 4", "category 5"], 0.45),
        (["earthquake", "magnitude"], 0.30),
        # Volcanic eruption — major eruptions are rare; supervolcano (Yellowstone) even rarer → ~5%
        (["volcanic eruption", "volcano erupts", "eruption of",
          "yellowstone", "supervolcano", "volcanic event",
          "lava flow", "pyroclastic"], 0.05),
        # Wildfires — specific acreage or destruction thresholds are uncertain → ~35%
        (["wildfire", "wildfires", "wildfire burns", "wildfire destroys",
          "acres burned", "acres scorched", "million acres burned",
          "wildfire season", "fire weather", "fire danger"], 0.35),
        # Macroeconomic — cuts/hikes/pauses depend on market pricing already
        (["rate cut", "rate hike", "interest rate cut", "interest rate hike",
          "raise rates", "raise interest rates", "lower rates", "lower interest rates",
          "cut rates", "hike rates", "fomc", "fed funds rate",
          "pause rates", "hold rates", "maintain rates", "rates unchanged",
          "rate pause", "rate hold", "rates on hold",
          "interest rates rise", "interest rates fall", "interest rates exceed",
          "interest rates drop", "interest rates above", "interest rates below",
          "rates rise above", "rates fall below", "rates exceed"], 0.50),
        (["recession", "in recession", "enters recession"], 0.25),
        # Housing market crash / real estate bust — tail event; ~15% in any given year
        # Placed BEFORE generic "fall below" / price-level patterns
        (["housing market crash", "housing crash", "real estate crash",
          "housing market collapse", "real estate collapse",
          "housing bubble burst", "housing bubble pop",
          "home prices crash", "home prices collapse"], 0.15),
        # Housing price direction — less extreme than crash; closer to 50/50
        (["housing prices", "home prices", "home values",
          "real estate prices", "median home price"], 0.50),
        (["default", "debt default", "sovereign default"], 0.10),
        # Trade/current account — near 50/50 like other macro level markets
        (["trade deficit", "trade surplus", "trade balance",
          "current account deficit", "balance of trade"], 0.50),
        # Interest rate levels (bond yields, treasury yields) — 50/50 threshold markets
        (["treasury yield", "10-year yield", "bond yield", "yield on the",
          "10-year treasury", "2-year treasury", "30-year treasury",
          "yield curve", "bund yield", "gilt yield"], 0.50),
        # Economic indicators — near 50/50 for specific threshold questions
        (["unemployment rate", "unemployment", "jobless rate", "nonfarm payroll",
          "jobs report", "labor market", "labor force"], 0.50),
        # Retail / consumer activity data — near 50/50 for monthly read threshold questions
        (["retail sales", "consumer spending", "consumer confidence",
          "consumer sentiment", "personal spending", "personal consumption",
          "durable goods", "factory orders", "industrial production"], 0.45),
        # Inflation threshold questions ("will inflation exceed 4%?") — bare "inflation" not
        # caught by "inflation rate" / "cpi" block below; treat as 50/50 threshold question
        (["inflation exceed", "inflation above", "inflation stay above",
          "inflation remain above", "inflation reach", "inflation drops below",
          "inflation falls below", "inflation returns to", "inflation target",
          "inflation stays below", "above the inflation", "below the inflation"], 0.50),
        (["inflation rate", "cpi", "pce", "consumer price index",
          "core inflation"], 0.50),
        (["gdp growth", "gdp contraction", "gdp shrinks",
          "gdp exceed", "gdp above", "gdp below", "gdp surpass",
          "economic growth", "economic contraction",
          "grow at", "growth rate", "growth of"], 0.50),
        # Stock index / financial index price levels — 50/50 by construction (like crypto)
        # Placed BEFORE generic "above $" / "below $" to avoid the 0.35 price-level pattern
        (["s&p 500 above", "s&p 500 below", "s&p 500 exceed", "s&p 500 reach",
          "s&p above", "s&p below",
          "dow jones above", "dow jones below",
          "nasdaq above", "nasdaq below", "nasdaq exceed",
          "vix above", "vix below",
          "sp500 above", "s&p500 above", "s&p500 below",
          "russell 2000 above", "russell 2000 below"], 0.50),
        # Earnings beat/miss — coin flip by definition (~50%); analysts recalibrate
        (["beat earnings", "beats earnings", "beat analyst", "beat analysts",
          "miss earnings", "misses earnings", "earnings beat", "earnings miss",
          "beat on earnings", "earnings per share above", "eps above",
          "eps beat", "earnings surprise", "earnings estimate"], 0.50),
        # FDA regulatory approval — more specific patterns must precede the general 0.40 entry.
        # PDUFA date confirmed: ~85-90% approval rate once NDA/BLA is under active review.
        (["pdufa", "pdufa date", "pdufa target date"], 0.85),
        # Clinical hold / FDA pause: active safety concern; approval very unlikely short-term → ~10%
        (["clinical hold", "clinical hold lifted", "fda clinical hold",
          "partial clinical hold"], 0.10),
        # Complete Response Letter (CRL) / resubmission — first review rejected; resubmission
        # outcome uncertain (~60%); placed BEFORE generic "fda approve" to catch resubmissions
        (["complete response letter", "crl issued", "received a crl",
          "resubmission", "resubmitted to the fda",
          "respond to the crl", "address the crl"], 0.60),
        # Advisory committee (adcom) vote uncertain — favorable vote → ~80%; unfavorable → ~30%
        # No directional signal without knowing vote outcome; use neutral 50% before FDA decision
        (["advisory committee", "fda advisory", "adcom", "fda panel",
          "fda panel vote", "fda panel meeting", "advisory panel",
          "fda advisory committee"], 0.50),
        # Regulatory approvals — must come BEFORE crypto ETF block ("spot etf" → 0.50)
        # and BEFORE merger/acquisition block ("merger" → 0.35)
        (["fda approve", "fda approval", "fda approves", "fda cleared",
          "fda authorization", "fda authorize", "fda clears"], 0.40),
        (["sec approve", "sec approves", "sec approval",
          "fcc approve", "fcc approves", "fcc approval",
          "ferc approve", "ferc approves", "ferc approval",
          "regulatory approval", "regulatory clearance",
          "cfpb approve", "ftc approve", "epa approve"], 0.40),
        # Crypto — price-level markets are 50/50 by definition
        (["bitcoin", "btc price", "btc above", "btc below",
          "ethereum", "eth price", "eth above", "eth below",
          "crypto", "cryptocurrency"], 0.50),
        (["bitcoin etf", "crypto etf", "ethereum etf",
          "spot etf", "etf approval"], 0.50),
        # Commodity / energy price threshold questions — gold, oil, metals, energy
        # Placed BEFORE generic "reach $"/"above $" (0.35) which requires a dollar sign
        # These are slightly above 0.35 because commodity trends are stickier than equities
        (["gold price", "gold prices", "gold above", "gold below",
          "gold exceed", "gold surpass", "price of gold", "gold reaches",
          "crude oil", "oil price", "oil prices", "oil above", "oil below",
          "brent crude", "wti crude", "price of oil", "barrel of oil",
          "natural gas price", "natural gas above", "natural gas below",
          "silver price", "copper price", "energy price", "energy prices",
          "commodity price", "commodity prices"], 0.40),
        # Price / market levels — mean-reversion roughly 50/50 near current levels
        (["reach $", "hits $", "hit $", "exceed $", "above $",
          "surpass $", "cross $", "break $", "top $"], 0.35),
        (["below $", "under $", "fall below", "drop below",
          "dip below", "dip to $"], 0.35),
        # Corporate events — low base rate, most announcements don't complete
        # IPO announcement timing markets: "when will X announce an IPO?"
        (["announce an ipo", "officially announce an ipo",
          "ipo announcement", "going public", "go public"], 0.25),
        (["ipo by", "ipo before", "initial public offering"], 0.30),
        # Sports debut/call-up markets: "will X make his MLB debut by Y?"
        (["make his mlb debut", "make her mlb debut",
          "play in a game for", "called up", "nhl debut",
          "nba debut", "make his debut", "make her debut"], 0.35),
        (["merger", "acquisition", "acquired by", "be acquired",
          "get acquired", "was acquired", "will acquire", "acquire a",
          "acquire the company", "acquire an", "take private",
          "buyout", "takeover", "be taken over", "be bought out"], 0.35),
        # Corporate market entry / expansion — entering a new business vertical → ~35%
        (["enter the market", "enter the healthcare", "enter the insurance",
          "enter the banking", "enter the auto", "enter the space",
          "launch a new business", "expand into",
          "new market entry", "move into the"], 0.35),
        # Production / delivery milestone — volume targets are uncertain → ~40%
        (["vehicle deliveries", "delivery target", "delivery milestone",
          "production target", "production milestone",
          "units delivered", "cars delivered", "deliveries in",
          "million deliveries", "million units"], 0.40),
        # Divestiture / forced sale — regulatory or activist-driven → ~35%
        # Placed separately from "merger" to catch "sold by" / "forced to sell" framing
        (["be sold by", "forced to sell", "forced sale", "divest",
          "divestiture", "forced divestiture", "sell off",
          "spin off its", "spin out"], 0.35),
        # Stock split — corporate event, relatively rare in any given 3-6 month window → ~20%
        (["stock split", "share split", "reverse stock split",
          "forward stock split", "split its stock", "announce a split"], 0.20),
        (["bankruptcy", "file for bankruptcy", "goes bankrupt",
          "go bankrupt", "declare bankruptcy", "seek bankruptcy"], 0.15),
        # Bank failure / financial crisis — systemic bank failures are rare → ~15%
        (["bank failure", "bank collapse", "banking crisis",
          "bank run", "bank bailout", "bank insolvency",
          "financial institution fail", "savings and loan"], 0.15),
        # Gun control / firearms legislation — rare Congressional action → ~20%
        (["gun control", "gun legislation", "firearms legislation",
          "assault weapons ban", "red flag law", "background check legislation",
          "firearms restriction", "gun safety legislation",
          "ban assault weapons", "gun law", "gun reform"], 0.20),
        # Currency / exchange rate markets — threshold questions → ~40%
        # Placed BEFORE tech/regulation block and generic price blocks
        (["exchange rate", "currency exchange", "depreciate", "depreciation",
          "appreciate against", "appreciate versus", "currency falls",
          "peso depreciate", "euro falls", "yen depreciate",
          "dollar strengthen", "dollar weaken", "dollar index"], 0.40),
        # Company valuation / market cap — similar to price-level markets (~35%)
        # Covers "Will X be valued above $50B?" style questions without a bare $
        (["be valued at", "be valued above", "valued above", "be worth",
          "worth more than", "market cap above", "market cap exceed",
          "market cap of", "valuation of", "valuation above",
          "valued at $", "valuation at $"], 0.35),
        # Tech competition / market position — new product vs incumbent → ~35%
        (["surpass github", "surpass google", "surpass microsoft",
          "surpass apple", "surpass amazon", "surpass meta",
          "market share above", "market share exceed",
          "beat google", "beat microsoft", "beat apple"], 0.35),
        # Social media age restriction / digital regulation — growing likelihood → ~30%
        (["age restriction", "age verification", "age limit for social media",
          "restrict social media", "restricted for minors", "restricted to minors",
          "social media age", "minors on social media",
          "age gate", "online age verification", "social media for minors",
          "ban for minors", "minors from social media"], 0.30),
        # Corporate leadership retention — "Will X remain CEO?" (~65%)
        # Contrast: fired/dismissed at 0.25 means staying is more likely
        (["remain ceo", "remain as ceo", "stay as ceo", "continue as ceo",
          "keep his job as", "keep her job as", "retain his position",
          "retain her position", "remain in office", "remain in power",
          "stay in power", "stay in office", "stay on as"], 0.65),
        # Tech / social media regulation — low base rate (regulation takes years)
        (["tiktok ban", "ban tiktok", "tiktok be banned", "ban on tiktok",
          "social media ban", "tech ban", "platform ban",
          "block tiktok", "ban chinese apps"], 0.20),
        # NATO Article 5 collective defense invocation — historically never used in combat → ~5%
        # Placed BEFORE generic "declare war"/"military strike" (0.15)
        (["invoke article 5", "article 5 of nato", "article 5 of the nato",
          "article 5 be invoked", "invoke nato's article 5",
          "nato's collective defense", "collective defense clause",
          "mutual defense clause", "article v of the nato"], 0.05),
        # Military territorial recapture / advance — active war campaigns have uncertain outcome → ~30%
        # Placed BEFORE "declare war" to avoid first-match with low base rate
        (["recapture", "retake", "reclaim territory", "liberate",
          "advance on", "military offensive", "push into",
          "counteroffensive", "seize territory", "capture the city",
          "take back", "overrun"], 0.30),
        # Independence referendum / self-determination vote — rare political events → ~15%
        (["referendum on independence", "independence referendum",
          "vote on independence", "vote on secession",
          "self-determination vote", "plebiscite on",
          "hold a referendum", "independence vote"], 0.15),
        # Military troop withdrawal / drawdown — planned withdrawals often delayed → ~30%
        # Placed BEFORE "declare war"/"invade" to avoid geopolitical catch-all
        (["troop withdrawal", "withdraw troops", "pull out troops",
          "military withdrawal", "military drawdown", "drawdown of troops",
          "troops leave", "forces leave", "exit afghanistan",
          "end the mission", "end combat operations",
          "remove troops from", "troops return home"], 0.30),
        # Civil war / internal armed conflict — specific-country risk (~25% in a 1-year window)
        # Placed BEFORE "declare war" (0.15) to catch internal conflict separately
        (["civil war", "armed conflict", "armed uprising",
          "insurgency", "rebel forces", "sectarian conflict",
          "internal conflict", "internal war", "militias", "warlord"], 0.25),
        # Geopolitical — low base rate for dramatic events
        (["declare war", "invade", "military strike", "launch attack"], 0.15),
        (["coup", "overthrow", "regime change"], 0.10),
        # Nobel Prize — single winner from hundreds of candidates worldwide → ~5-10%
        # Must come BEFORE generic " win " catch-all and entertainment awards
        (["nobel prize", "nobel peace prize", "nobel laureate",
          "win the nobel", "receive the nobel"], 0.10),
        # Pulitzer Prize — small field of finalists, journalism/arts awards → ~10%
        # Must come BEFORE " win " catch-all
        (["pulitzer prize", "pulitzer", "win the pulitzer"], 0.10),
        # Entertainment awards — single winner from ~5 nominees → ~20%
        # Must come BEFORE generic entertainment (streaming/movie/film at 0.25) and " win " catch-all
        (["grammy", "oscar", "academy award", "palme d'or",
          "emmy award", "golden globe award", "tony award", "bafta award",
          "sag award", "screen actors guild", "sundance award"], 0.20),
        # Renewable energy supply thresholds — energy transition is accelerating → ~40%
        (["renewable energy", "solar energy supply", "wind energy supply",
          "clean energy supply", "electricity from renewables",
          "green energy percentage", "renewables share",
          "renewable electricity", "clean electricity"], 0.40),
        # Political scandal — high-visibility administrations have frequent scandals → ~45%
        (["political scandal", "sex scandal", "financial scandal",
          "corruption scandal", "bribery scandal", "abuse of power",
          "misconduct scandal", "cover-up", "whistleblower alleges",
          "kickback scheme"], 0.45),
        # Housing market correction — milder than crash; ~20% decline threshold → ~20%
        # Placed AFTER housing crash (0.15) — overlap is fine; "correction" is distinct from "crash"
        (["housing correction", "home price correction", "real estate correction",
          "housing market correct", "home prices correct", "prices correct",
          "housing downturn", "housing slowdown", "price correction",
          "market correction"], 0.20),
        # Autonomous vehicle / self-driving car deployment — ambitious timeline → ~25%
        # "Announced" ≠ deployed; treat like Rule 3 (announced vs completed)
        (["autonomous vehicle", "self-driving car", "robotaxi",
          "full self-driving", "autonomous taxi",
          "level 4 autonomy", "level 5 autonomy", "fully autonomous driving",
          "driverless car", "driverless vehicle"], 0.25),
        # Quantum computing / tech breakthroughs — long-horizon, low near-term probability → ~10%
        (["quantum computing", "quantum supremacy", "quantum advantage",
          "break encryption", "quantum computer", "quantum error correction",
          "fault-tolerant quantum"], 0.10),
        # Mars / deep space mission — beyond moon; very ambitious timeline → ~10-15%
        # Placed AFTER general NASA block (0.30) to catch specifically Mars/deep space framing
        (["mars mission", "mission to mars", "manned mars", "crewed mars",
          "human mission to mars", "mars landing", "mars orbit",
          "mars colony", "deep space mission", "interplanetary"], 0.15),
        # Concert / live music — artists tour regularly; if market is open, tour is likely → ~45%
        # Placed BEFORE generic entertainment (0.25) because tours materialize more reliably
        (["concert tour", "world tour", "go on tour", "announce a tour",
          "headlining tour", "headline tour", "headline a tour",
          "north american tour", "european tour", "stadium tour",
          "arena tour", "announce tour dates", "tour dates announced"], 0.45),
        # Tech product announcements — established companies (Apple, Samsung) are reliable (~55%)
        # specific-quarter timing is uncertain; placed BEFORE generic "launch" (0.35)
        (["new iphone", "iphone 17", "iphone 18", "iphone 19", "iphone 20",
          "new ipad", "new mac", "new macbook", "new apple",
          "apple announces", "apple reveal",
          "samsung galaxy", "new galaxy", "galaxy s", "galaxy flagship",
          "pixel phone", "new pixel",
          "ar glasses", "ar headset", "vr headset", "smart glasses",
          "mixed reality headset", "vision pro", "next-gen headset",
          "product announcement", "product reveal", "announce a new"], 0.55),
        # Media / entertainment — very low: release dates often slip
        # "release" and "show" excluded — too broad (hits Fed minutes, data reports, etc.)
        # "season" kept but specific entertainment-flavored phrases handle most cases
        (["premieres", "premiere by", "movie release", "film release",
          "tv show", "television show", "new season", "season finale",
          "season premiere", "sequel", "spin-off",
          "box office", "streaming", "in theaters", "in cinemas",
          "music video", "album drops", "album release",
          "official trailer", "teaser trailer", "trailer release", "trailer for",
          "episode", "documentary"], 0.25),
        # "season" alone is too broad (matches wildfire season, flu season, etc.)
        # Explicit numbered seasons + movie/film catch-all
        (["season 2", "season 3", "season 4", "season 5",
          "season 6", "season 7", "season 8", "season 9",
          "movie", "film"], 0.25),
        # Space / aerospace — launch delays are the norm; SpaceX has better cadence than NASA
        (["starship", "falcon heavy", "falcon 9",
          "spacex launch", "rocket launch"], 0.40),    # SpaceX has high cadence
        (["nasa", "moon landing", "lunar gateway", "artemis",
          "space station", " iss ", "james webb",
          "land on the moon", "land astronauts on the moon",
          "crewed lunar", "lunar lander", "lunar module"], 0.30),  # space missions often delayed
        # Health / pandemic / drug trials
        (["phase 3", "clinical trial", "phase 2",
          "drug trial", "clinical study"], 0.35),      # Phase 3 trials ~35-50% success
        (["pandemic", "epidemic", "outbreak",
          "public health emergency"], 0.25),           # Low base rate for declared emergencies
        # COVID / virus variant classification — "variant of concern" declared occasionally → ~30%
        (["variant of concern", "covid variant", "new variant",
          "sars-cov", "covid strain", "virus variant",
          "declare a public health emergency", "mpox", "monkeypox"], 0.30),
        # Health / mortality markets — "will X die/survive before Y?"
        # Very specific phrases to avoid false positives from medical policy markets
        (["die before", "die by", "pass away before", "pass away by",
          "survive until", "still alive by", "alive by",
          "death before", "death by date"], 0.15),
        # Climate records / temperature anomalies — warming trend makes record years frequent → ~40%
        (["hottest year", "warmest year", "record temperature",
          "temperature record", "record heat", "record warming",
          "coldest year", "coldest winter", "record cold",
          "climate record", "record rainfall", "record drought",
          "record snowfall", "all-time record"], 0.40),
        # Climate / renewable energy policy
        (["carbon tax", "carbon credit", "net zero",
          "emissions target", "paris agreement",
          "clean energy", "renewable energy mandate"], 0.35),
        # AI / technology model release timing — similar to entertainment: announced ≠ shipped
        # "will OpenAI release GPT-5 by Q3?" — base rate ~25% for any given 3-month window
        (["gpt-5", "gpt-6", "gpt 5", "gpt 6",
          "claude 4", "claude 5", "claude-4", "claude-5",
          "gemini 2", "gemini 3", "gemini ultra",
          "llama 4", "llama-4", "llm release", "ai model release",
          "release a new model", "release their next model",
          "agi by", "artificial general intelligence by",
          "achieved artificial general intelligence",
          "achieve artificial general intelligence",
          "claims agi", "declare agi", "announce agi",
          "claims to have achieved agi"], 0.25),
        # AI capability milestones — AI passing high-stakes tests is increasingly common → ~40%
        (["ai pass", "ai passes", "ai score", "ai scores",
          "ai outperform", "ai beat", "ai beats",
          "ai achieve", "artificial intelligence pass",
          "llm pass", "language model pass",
          "ai take the bar", "ai take the mcat", "ai pass the",
          "machine learning achieve"], 0.40),
        # AI regulation / governance — growing legislative push → ~30%
        (["ai regulation", "regulate ai", "ban ai", "ai ban",
          "ai law", "ai legislation", "ai governance",
          "artificial intelligence regulation",
          "autonomous weapons ban", "lethal autonomous weapons",
          "ai safety law", "ai liability"], 0.30),
        # Trade / tariffs — politically uncertain, executive action somewhat common
        (["tariff on", "tariffs on", "tariff rate", "impose a tariff",
          "tariff increase", "tariff reduction", "trade war",
          "trade deal", "trade agreement"], 0.40),
        # Immigration / deportation — executive action, moderate base rate
        (["deport", "deportation", "mass deportation",
          "immigration ban", "border wall", "sanctuary city",
          "immigration bill", "immigration legislation", "immigration reform",
          "immigration law", "immigration policy"], 0.35),
        # Approval ratings — market already prices current polling; near 50/50
        (["approval rating", "job approval", "favorability rating",
          "approve of the", "disapprove of the", "net approval"], 0.50),
        # Labor strikes / work stoppages
        (["go on strike", "labor strike", "workers strike", "union strike",
          "strike action", "work stoppage", "walkout"], 0.30),
        # Sports awards / honors — single winner from many candidates
        (["mvp", "cy young", "rookie of the year", "heisman",
          "hall of fame", "all-star", "golden glove", "best player"], 0.20),
        # Sports playoffs / championships — any given team ~30-40% pre-season
        # "qualify for champions league" must come BEFORE the general "champions league" 0.50 check
        (["make the playoffs", "reach the playoffs", "qualify for",
          "qualify for the champions league", "advance to", "make it to",
          "clinch a playoff"], 0.35),
        # Sports trades / signings — rumors often don't materialize
        (["get traded", "be traded", "trade deadline", "sign with",
          "free agent signing", "sign a contract", "extension"], 0.30),
        # Corporate appointment / leadership change
        # "become ceo" / "be named ceo" — moderate base rate; board decisions hard to predict
        (["become ceo", "be named ceo", "be appointed ceo", "new ceo",
          "become the ceo", "named as ceo", "appoint a new ceo",
          "become cfo", "be named cfo", "new cfo",
          "become chair", "be named chair", "become chairman"], 0.35),
        (["launch", "launches", "launched by", "launches by"], 0.35),
        # Generic sports/competition catch-all — must come LAST
        # " win " (with spaces) catches "Will X win [any competition]?"
        ([" win "], 0.52),
    ]
    for signals, rate in heuristics:
        if any(s in title for s in signals):
            return rate

    return None


def tag_watchlist_overlap(
    markets: list[dict],
    watchlist_tickers: set[str],
    ticker_details: dict | None = None,
    stale: bool = False,
) -> list[dict]:
    """
    Mark markets that overlap with smart money watchlist positions.
    Sets m['watchlist_signal'] = True on any market whose Kalshi ticker appears
    in the pre-built set of cross-referenced tickers.
    If ticker_details is provided (from latest_signals.json), also annotates:
      - m['watchlist_direction']: consensus YES/NO/MIXED/UNKNOWN
      - m['watchlist_position_val']: total $ smart money behind this ticker
      - m['watchlist_trader_count']: number of traders
    If stale=True (scan data older than 24h), sets m['watchlist_stale']=True
    so the pre-sort score and prompt can apply a discount.
    """
    for m in markets:
        ticker = m.get("ticker", "")
        hit = ticker in watchlist_tickers
        m["watchlist_signal"] = hit
        if hit:
            m["watchlist_stale"] = stale
            if ticker_details:
                detail = ticker_details.get(ticker, {})
                m["watchlist_direction"]    = detail.get("consensus_direction", "UNKNOWN")
                m["watchlist_position_val"] = detail.get("total_position_val", 0.0)
                m["watchlist_trader_count"] = detail.get("trader_count", 0)
        elif not hit:
            m.setdefault("watchlist_direction", None)
            m.setdefault("watchlist_position_val", None)
            m.setdefault("watchlist_trader_count", None)
            m.setdefault("watchlist_stale", False)
    return markets


def compute_spread_signal(yes_bid: float, yes_ask: float, mid: float) -> dict:
    """
    Bid/ask spread as % of mid price.
    Wide spread (>5%) = market maker uncertainty = potential mispricing.
    This is context for Claude, not a standalone flag trigger.
    """
    if mid <= 0 or yes_bid <= 0 or yes_ask <= 0:
        return {"spread_pct": None, "spread_wide": False}
    spread_pct = (yes_ask - yes_bid) / mid
    return {"spread_pct": round(spread_pct, 4), "spread_wide": spread_pct > 0.05}


def compute_drift_signal(
    mid: float,
    market: dict,
    drift_min_abs: float = 0.0,
    drift_min_pct: float = 0.05,
) -> dict:
    """
    Drift between current order-book mid and the last traded price.
    Requires BOTH a minimum absolute move AND a minimum percentage move to flag,
    preventing tiny cent-level moves at very low prices from triggering on pct alone.
    Thresholds come from config (markets.drift_min_abs / markets.drift_min_pct).
    """
    last = float(market.get("last_price_dollars") or 0)
    if not last or mid is None:
        return {"price_drift": None, "price_drift_abs": None, "drift_flag": False}
    abs_drift = abs(mid - last)
    pct_drift = abs_drift / last
    drift_flag = abs_drift > drift_min_abs and pct_drift > drift_min_pct
    return {
        "price_drift":     round((mid - last) / last, 4),
        "price_drift_abs": round(abs_drift, 4),
        "drift_flag":      drift_flag,
    }


def compute_whale_reversal(market: dict, whale: dict | None) -> bool:
    """
    True when whale trade direction opposes the recent price trend.
    Informed money trading against momentum = strong contrarian signal.
    Uses previous_price_dollars vs current mid for the trend direction.
    """
    if not whale or not whale.get("whale_detected"):
        return False
    whale_dir = whale.get("whale_direction")
    if not whale_dir:
        return False

    yes_bid = float(market.get("yes_bid_dollars") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or 0)
    prev = float(market.get("previous_price_dollars") or 0)
    if not prev or not (yes_bid + yes_ask):
        return False

    mid = (yes_bid + yes_ask) / 2
    trend_up = mid > prev
    whale_bullish = whale_dir == "YES"
    return whale_bullish != trend_up  # opposite direction = reversal


def compute_orderbook_signal(orderbook: dict) -> dict:
    """
    Computes bid/ask depth imbalance from the full order book.

    Imbalance = bid_depth / (bid_depth + ask_depth)
    > 0.65 → more buyers → YES may be underpriced
    < 0.35 → more sellers → YES may be overpriced

    Handles multiple Kalshi orderbook response shapes defensively.
    """
    empty = {"ob_bid_depth": None, "ob_ask_depth": None,
             "ob_imbalance": None, "ob_flag": False, "ob_direction": None}

    if not orderbook:
        return empty

    def _extract_levels(data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("levels") or data.get("orders") or []
        return []

    def _sum_sizes(levels) -> float:
        total = 0.0
        for lvl in levels:
            if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                total += float(lvl[1])
            elif isinstance(lvl, dict):
                total += float(lvl.get("size") or lvl.get("quantity") or 0)
        return total

    # Kalshi may nest under "yes" key or at top level
    yes_book = orderbook.get("yes") or orderbook
    bids = _extract_levels(yes_book.get("bids") or yes_book.get("bid") or [])
    asks = _extract_levels(yes_book.get("asks") or yes_book.get("ask") or [])

    bid_depth = _sum_sizes(bids)
    ask_depth = _sum_sizes(asks)
    total     = bid_depth + ask_depth

    if total == 0:
        return empty

    imbalance = bid_depth / total
    ob_flag   = imbalance > 0.65 or imbalance < 0.35
    direction = "YES" if imbalance > 0.65 else ("NO" if imbalance < 0.35 else None)

    return {
        "ob_bid_depth": round(bid_depth, 2),
        "ob_ask_depth": round(ask_depth, 2),
        "ob_imbalance": round(imbalance, 3),
        "ob_flag":      ob_flag,
        "ob_direction": direction,
    }


def score_market(market: dict, config: dict) -> dict:
    """
    Scores a single market for mispricing.

    Returns the market enriched with mid_price, base_rate, raw_edge, flag,
    flag_path, spread_wide, spread_pct, price_drift, and drift_flag.

    Flag behaviour is controlled by config.markets.flag_mode (default "passthrough"):

      "passthrough" (default / baseline)
        flag if: raw_edge > threshold  OR  base_rate is None  OR  drift
        This is the original behaviour — every priced market without a
        matching heuristic is automatically a candidate.

      "strict_anomaly_only"
        flag ONLY if: drift_flag is True
        (whale_detected would also trigger here, but whale detection runs in
        main.py step 5, *after* score_market runs in step 3, so whale state
        is unavailable at this point. main.py applies whale_reversal and
        ob_flag post-hoc to set flag=True for whale markets.)
        base_rate and raw_edge are still computed and returned for Claude
        context, but do not trigger the flag under this mode.

      "strict_with_heuristic"
        flag if: drift_flag  OR  (base_rate is not None AND raw_edge > threshold)
        Adds back the heuristic base-rate edge as a trigger on top of
        strict_anomaly_only.  A market whose heuristic estimate disagrees
        meaningfully with the current price is included; pure BR_NONE markets
        (no matching heuristic) are still excluded.

    whale_reversal is merged into flag by main.py after step 5 regardless of mode.
    """
    mkt_cfg        = config.get("markets", {})
    edge_threshold = mkt_cfg.get("edge_threshold", 0.08)
    flag_mode      = mkt_cfg.get("flag_mode", "passthrough")
    drift_min_abs  = mkt_cfg.get("drift_min_abs", 0.0)
    drift_min_pct  = mkt_cfg.get("drift_min_pct", 0.05)

    yes_bid = float(market.get("yes_bid_dollars") or market.get("yes_bid") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or market.get("yes_ask") or 0)

    if yes_bid > 0 and yes_ask > 0:
        mid_price = (yes_bid + yes_ask) / 2
    else:
        # One-sided or empty book — use last traded price as best available estimate
        last_p = float(market.get("last_price_dollars") or 0)
        mid_price = last_p if last_p > 0 else None

    base_rate = estimate_base_rate(market)

    if mid_price is not None and base_rate is not None:
        raw_edge = abs(base_rate - mid_price)
    else:
        raw_edge = None

    # Net-of-spread edge: subtract half the bid-ask spread from raw_edge.
    # Entering at ask (not mid) means you pay half the spread on entry.
    # net_edge < 0 means the spread consumes the entire theoretical edge.
    half_spread = (yes_ask - yes_bid) / 2 if (yes_bid > 0 and yes_ask > 0) else 0
    net_edge = round(raw_edge - half_spread, 6) if raw_edge is not None else None

    spread = compute_spread_signal(yes_bid, yes_ask, mid_price or 0)
    drift  = compute_drift_signal(mid_price or 0, market, drift_min_abs, drift_min_pct)

    # Heuristic direction: which way the base rate leans vs current market price.
    # 5pp buffer avoids noise at near-neutral pricing.
    if base_rate is not None and mid_price is not None:
        if base_rate > mid_price + 0.005:
            heuristic_direction = "YES"
        elif base_rate < mid_price - 0.005:
            heuristic_direction = "NO"
        else:
            heuristic_direction = "NEUTRAL"
    else:
        heuristic_direction = None

    # Short-horizon flag: markets closing within 7 days require a higher edge
    # bar (Rule 28: 15pp) because heuristic base rates are long-run averages,
    # not calibrated to specific 1-7 day windows.
    is_short_horizon = market.get("time_horizon") in ("INTRADAY", "WEEKLY")
    short_edge_threshold = mkt_cfg.get("short_horizon_edge_threshold", 0.15)

    # Effective edge threshold for flagging: elevated for short-horizon markets.
    flag_edge_threshold = short_edge_threshold if is_short_horizon else edge_threshold

    # All signals computed independently of flag_mode — truthful regardless of branch order.
    has_edge    = raw_edge is not None and raw_edge > edge_threshold          # informational
    flag_edge   = raw_edge is not None and raw_edge > flag_edge_threshold     # for flagging
    has_drift   = drift["drift_flag"]
    has_br_none = base_rate is None and mid_price is not None

    flag      = False
    flag_path = None   # "EDGE" | "BR_NONE" | "DRIFT" | "HEURISTIC" | None

    if flag_mode == "passthrough":
        if flag_edge:
            flag, flag_path = True, "EDGE"
        elif base_rate is None and mid_price is not None:
            flag, flag_path = True, "BR_NONE"
        elif has_drift:
            flag, flag_path = True, "DRIFT"

    elif flag_mode == "strict_anomaly_only":
        if has_drift:
            flag, flag_path = True, "DRIFT"

    elif flag_mode == "strict_with_heuristic":
        if has_drift:
            flag, flag_path = True, "DRIFT"
        elif base_rate is not None and flag_edge:
            flag, flag_path = True, "HEURISTIC"

    else:
        raise ValueError(
            f"Unknown flag_mode {flag_mode!r}. "
            "Expected: passthrough | strict_anomaly_only | strict_with_heuristic"
        )

    return {
        **market,
        "mid_price":           mid_price,
        "base_rate":           base_rate,
        "raw_edge":            raw_edge,
        "net_edge":            net_edge,
        "heuristic_direction": heuristic_direction,
        "flag":                flag,
        "flag_path":           flag_path,
        "flag_mode":           flag_mode,
        "short_horizon":       is_short_horizon,
        # Per-signal presence — always set, independent of mode and branch order.
        "sig_edge":      has_edge,
        "sig_drift":     has_drift,
        "sig_br_none":   has_br_none,
        "time_horizon":  market.get("time_horizon", "MONTHLY"),
        **spread,
        **drift,
    }


def score_markets(markets: list[dict], config: dict) -> list[dict]:
    """Scores all filtered markets and returns them sorted by priority."""
    scored = [score_market(m, config) for m in markets]
    # Sort: watchlist-overlap first, then flagged, then by edge desc
    scored.sort(key=lambda m: (
        not m.get("watchlist_signal", False),
        not m.get("flag", False),
        -(m.get("raw_edge") or 0),
    ))
    return scored
