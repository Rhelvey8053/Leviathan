from datetime import datetime, timezone, timedelta
from core.fees import kalshi_fee

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
      7. Closing within min_hours_to_close (not actionable by report delivery time)
      8. Ticker starts with a prop_market_exclude_prefixes prefix (gimmick/
         novelty markets with no real predictive skill ceiling, e.g.
         "what will the announcer say" -- these can be liquid enough to
         clear every other filter, confirmed empirically 2026-08-01: 10/36
         near-dated markets sampled were KXMLBMENTION and survived filters
         1-7 cleanly)
    """
    cfg          = config.get("markets", {})
    global_min_vol  = cfg.get("min_volume", 500)
    max_vol         = cfg.get("max_volume_filter", 75000)
    min_days        = cfg.get("min_days_to_close", 0)
    max_days        = cfg.get("max_days_to_close", 180)
    min_hours       = cfg.get("min_hours_to_close", 6)
    min_price       = cfg.get("min_market_price", 0.05)
    max_price       = cfg.get("max_market_price", 0.95)
    min_oi          = cfg.get("min_open_interest", 0)
    bucket_vol      = cfg.get("bucket_min_volume", {})
    keywords        = [k.lower() for k in cfg.get("efficient_market_keywords", [])]
    prop_prefixes   = tuple(cfg.get("prop_market_exclude_prefixes", ["KXMLBMENTION"]))

    now        = datetime.now(timezone.utc)
    min_close  = now + timedelta(days=min_days)
    min_close  = max(min_close, now + timedelta(hours=min_hours))
    max_close  = now + timedelta(days=max_days)

    filtered = []
    for m in markets:
        ticker = m.get("ticker", "")
        if ticker.startswith(prop_prefixes):
            continue

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


_HEURISTIC_RULES: list[tuple[list[str], float, str]] = [
    (['win the world series', 'win world series'], 0.5, 'sports championship'),
    (['win the championship', 'win the nba', 'win the nfl', 'win the cup', 'win the world cup', 'win the fifa', 'world cup winner', 'world series winner', 'win the champions league', 'champions league winner', 'stanley cup'], 0.5, 'sports championship'),
    (['win the super bowl', 'super bowl winner'], 0.5, 'sports championship'),
    (['win the game', 'win on', 'win their next'], 0.52, 'sports game'),
    (['win the election', 'win election', 'wins the election', 'win the primary', 'win the runoff'], 0.52, 'election'),
    (['win the presidency', 'win the white house'], 0.5, 'presidential election'),
    (['win the senate race', 'win the house race', 'win the gubernatorial', 'win the mayoral', 'mayor race', 'win the governor'], 0.52, 'down-ballot election'),
    (['be reelected', 'win reelection', 'win re-election', 'reelected', 're-elected', 'secure a second term', 'win a second term', 'second presidential term'], 0.52, 'reelection'),
    (['primary challenge', 'primary challenger', 'face a primary', 'challenge in the primary', 'defeated in the primary', 'lose the primary', 'lost the primary', 'primary opponent'], 0.3, 'primary challenge'),
    (['withdraw from the paris', 'withdraw from the iran', 'withdraw from the jcpoa', 'withdraw from the npt', 'withdraw from the wto', 'withdraw from the who', 'withdraw from the un ', 'withdraw from the nato', 'withdraw from the treaty', 'withdraw from the agreement', 'withdraw from the accord', 'withdraw from the convention', 'withdraw from nato', 'leave the eu', 'exit the eu', 'leave nato', 'pull out of the treaty', 'pull out of the agreement', 'exit the agreement', 'exit the accord', 'exit the treaty', 'renounce the treaty'], 0.2, 'treaty withdrawal'),
    (['suspend his campaign', 'suspend her campaign', 'end his campaign', 'end her campaign', 'withdraw his candidacy', 'withdraw her candidacy', 'exit the race', 'quit the race', 'drop out of the', 'withdraw from the'], 0.3, 'candidate withdrawal'),
    (['special election', 'special senate election', 'special house election', 'special congressional election', 'fill the vacancy', 'senate vacancy', 'house vacancy'], 0.45, 'special election'),
    (['constitutional amendment', 'amend the constitution', 'constitutional convention', 'repeal the 2nd amendment', 'repeal the amendment', 'electoral college amendment', 'equal rights amendment', 'balanced budget amendment', 'abolish the electoral college', 'electoral college be abolished', 'eliminate the electoral college', 'abolish the electoral'], 0.05, 'constitutional amendment'),
    (['recall election', 'recall the governor', 'recall the mayor', 'recall vote', 'recall campaign', 'recall effort', 'recall petition', 'remove the governor', 'remove the mayor', 'recall referendum', 'recall initiative'], 0.15, 'recall election'),
    (['snap election', 'early election', 'early general election', 'call for early elections', 'dissolve parliament', 'call a general election'], 0.25, 'snap election'),
    (['not seek a second term', 'not seek reelection', 'not seek re-election', 'will not run for', 'choose not to run', 'choosing not to run', 'decided not to run', 'decide not to run', 'not stand for reelection', 'not stand for re-election'], 0.3, 'candidate withdrawal'),
    (['announce his candidacy', 'announce her candidacy', 'announce their candidacy', 'declare his candidacy', 'declare her candidacy', 'launch his campaign', 'launch her campaign', 'enter the race', 'join the race', 'file to run', 'announce a run for', 'announce a bid for', 'officially enter the race', 'formally enter the race', 'announce a presidential bid', 'announce a senate bid', 'announce a gubernatorial bid'], 0.35, 'candidacy announcement'),
    (['ballot disqualification', 'ineligible for the ballot', 'kicked off the ballot', 'barred from the ballot', 'disqualified from the', 'removed from the ballot', 'disqualified from running', 'disqualified from appearing'], 0.2, 'ballot disqualification'),
    (['student loan forgiveness', 'student loan cancellation', 'student debt cancellation', 'student loan relief', 'cancel student debt', 'student debt forgiveness', 'student loan discharge'], 0.3, 'student loan forgiveness'),
    (['healthcare reform', 'health care reform', 'healthcare system be reformed', 'healthcare system reformed', 'health care system', 'universal healthcare', 'universal health care', 'medicare for all', 'medicaid expansion', 'affordable care act', 'health insurance reform', 'single payer', 'public option'], 0.2, 'healthcare reform'),
    (['minimum wage', 'raise the minimum wage', 'increase the minimum wage', 'minimum wage increase', 'minimum wage hike', 'minimum wage legislation', 'federal minimum wage', 'minimum wage to $', 'minimum wage bill'], 0.25, 'minimum wage legislation'),
    (['veto', 'presidential veto', 'veto the bill', 'pocket veto'], 0.2, 'presidential veto'),
    (['income tax', 'corporate tax', 'capital gains tax', 'estate tax', 'tax cut', 'tax hike', 'tax increase', 'tax decrease', 'tax reduction', 'tax reform bill', 'tax legislation', 'tax bill', 'federal tax', 'individual tax', 'tax overhaul', 'tcja extension', 'tax code reform', 'tax reform'], 0.35, 'tax legislation'),
    (['continuing resolution', 'omnibus bill', 'appropriations bill', 'government funding bill', 'spending bill', 'federal budget', 'budget resolution', 'budget deal', 'budget agreement', 'pass the budget', 'budget bill', 'budget deadline'], 0.4, 'budget/spending legislation'),
    (['pass the senate', 'pass the house', 'pass congress', 'pass in the senate', 'pass in the house', 'pass into law', 'signed into law', 'sign into law', 'pass the bill', 'passes the bill', 'pass legislation', 'become law', 'enacted into law', 'senate pass', 'house pass', 'senate approve', 'house approve', 'senate vote on', 'house vote on'], 0.35, 'legislative passage'),
    (['national emergency', 'declare a national emergency', 'declare an emergency', 'emergency declaration', 'invoke emergency powers', 'state of emergency', 'invoke the national emergencies act', 'invokes emergency powers', 'emergency powers act'], 0.25, 'national emergency'),
    (['executive order', 'sign an executive order', 'issue an executive order'], 0.45, 'executive order'),
    (['senate confirmation', 'confirmed by the senate', 'cabinet nomination', 'confirmed as secretary', 'confirmed as director', 'confirmed as ambassador'], 0.55, 'senate confirmation'),
    (["member of trump's cabinet", 'trump cabinet member', 'member of the cabinet leave', 'cabinet member leave', 'leave the cabinet', 'depart from the cabinet'], 0.65, 'cabinet departure'),
    (['resign', 'step down', 'stepping down', 'resigns from', 'resignation'], 0.2, 'resignation'),
    (['announce retirement', 'announce his retirement', 'announce her retirement', 'retire from the nba', 'retire from the nfl', 'retire from the mlb', 'retire from the nhl', 'retire from pro', 'retire from professional', 'retire from football', 'retire from basketball', 'retire from baseball', 'decide to retire', 'officially retire', 'retirement before', 'retirement announcement', 'announce they will retire', 'retire from the'], 0.3, 'athlete retirement'),
    (['pardon', 'presidential pardon', 'commute the sentence'], 0.35, 'presidential clemency'),
    (['control the senate', 'senate majority', 'senate control', 'majority in the senate', 'control of the senate', 'control the house', 'house majority', 'house control', 'majority in the house', 'control of the house', 'congressional majority', 'take control of the senate', 'take control of the house', 'flip the senate', 'flip the house', 'senate seat', 'senate race 20'], 0.5, 'congressional control'),
    (['lift sanctions', 'remove sanctions', 'ease sanctions', 'waive sanctions'], 0.2, 'sanctions removal'),
    (['impose sanctions', 'new sanctions', 'sanctions on', 'sanctions against'], 0.45, 'sanctions imposition'),
    (['nuclear power plant', 'nuclear plant accident', 'nuclear reactor', 'nuclear meltdown', 'nuclear accident', 'nuclear incident', 'chernobyl', 'fukushima', 'reactor failure', 'reactor meltdown'], 0.05, 'nuclear accident'),
    (['develop a nuclear weapon', 'develop nuclear weapons', 'acquire nuclear weapons', 'acquire nuclear capability', 'become a nuclear power', 'nuclear weapons program', 'achieve nuclear capability', 'nuclear armed', 'nuclear warhead', 'nuclear device'], 0.05, 'nuclear proliferation'),
    (['nuclear deal', 'nuclear agreement', 'nuclear accord', 'nuclear treaty', 'npt', 'iaea agreement'], 0.2, 'nuclear deal'),
    (['end enrichment', 'stop enrichment', 'halt enrichment', 'cease enrichment', 'enrichment of uranium', 'surrender enriched uranium', 'uranium stockpile', 'agrees to end uranium', 'uranium enrichment deal', 'uranium enrichment agreement'], 0.2, 'uranium enrichment'),
    (['martial law', 'declare martial law', 'impose martial law', 'invoke martial law', 'martial law declared', 'state of martial law'], 0.05, 'martial law'),
    (['regime fall', 'regime falls', 'regime collapse', 'government falls', 'government collapse', 'lose power', 'loses power', 'overthrown', 'government overthrown', 'coup attempt', 'fall of the government', 'fall of the regime', 'leadership change', 'leadership transition', 'end of the regime', 'end of the government'], 0.1, 'regime collapse'),
    (['peace deal', 'ceasefire', 'peace agreement', 'armistice'], 0.25, 'ceasefire or peace deal'),
    (['abraham accords', 'normalization with israel', 'normalize with israel', 'normalize relations with israel', 'normalize ties with israel', 'israel normalization', 'israel normalize', 'israel and saudi', 'saudi and israel', 'saudi-israel', 'uae-israel', 'gulf normalization'], 0.2, 'Abraham Accords'),
    (['join nato', 'nato membership', 'nato expansion', 'nato accession'], 0.35, 'NATO accession'),
    (['join brics', 'join the brics', 'brics membership', 'brics expansion', 'brics member', 'added to brics', 'new brics member', 'join the sco', 'sco membership', 'join the shanghai cooperation'], 0.3, 'BRICS expansion'),
    (['rejoin the paris', 'rejoin paris', 'rejoin the un', 'rejoin the who', 'rejoin the tpp', 'return to the agreement', 're-enter the agreement', 'rejoin the deal', 'return to the accord', 're-enter the paris', 'rejoin the accord'], 0.25, 'international agreement rejoin'),
    (['join the eu', 'eu membership', 'eu accession', 'european union membership'], 0.25, 'EU accession'),
    (['common currency', 'shared currency', 'unified currency', 'adopt a currency', 'currency union', 'monetary union', 'replace the dollar', 'replace the euro', 'petrodollar'], 0.1, 'currency union'),
    (['central bank digital currency', 'cbdc', 'digital dollar', 'digital euro', 'digital yuan', 'digital renminbi', 'e-cny', 'programmable money', 'retail cbdc', 'wholesale cbdc', 'cbdc launch', 'cbdc pilot', 'digital pound'], 0.15, 'CBDC adoption'),
    (['outperform', 'underperform', 'outgrow', 'grow faster than', 'perform better than', 'exceed average', 'below average growth', 'economic performance'], 0.5, 'economic performance comparison'),
    (['un security council', 'united nations security council', 'security council resolution', 'security council vote', 'pass at the un', 'un resolution'], 0.15, 'UN Security Council'),
    (['recognize', 'diplomatic recognition', 'normalize relations', 'establish relations'], 0.3, 'diplomatic recognition'),
    (['bilateral summit', 'diplomatic summit', 'peace summit', 'summit between', 'summit with', 'diplomatic meeting', 'state visit by', 'bilateral meeting', 'meet with xi', 'meet with putin', 'meet with kim', 'diplomatic talks between', 'diplomatic negotiations', 'diplomatic engagement'], 0.4, 'diplomatic summit'),
    (['supreme court', 'scotus', 'high court ruling', 'appeals court', 'circuit court'], 0.5, 'supreme court ruling'),
    (['overturns', 'upholds', 'rules in favor', 'strikes down', 'court ruling', 'court decision'], 0.5, 'court ruling'),
    (['be pardoned', 'receive a pardon', 'presidential pardon', 'pardon of', 'receive clemency', 'clemency for', 'commute his sentence', 'commute her sentence', 'commute the sentence', 'commute their sentence', 'commuted sentence', 'grant a pardon', 'grant clemency'], 0.35, 'presidential clemency'),
    (['plead guilty', 'plea deal', 'plea agreement', 'enter a guilty plea', 'no contest plea', 'accept a plea', 'negotiate a plea'], 0.45, 'plea deal'),
    (['be acquitted', 'found not guilty', 'not guilty verdict', 'acquitted of', 'acquittal', 'declared not guilty', 'ruled not guilty'], 0.35, 'acquittal'),
    (['convicted', 'found guilty', 'indicted', 'charged with'], 0.4, 'criminal conviction'),
    (['divorce settlement', 'divorce finalized', 'divorce is finalized', 'custody battle', 'custody ruling', 'custody decision', 'custody settlement', 'child custody', 'civil settlement', 'civil suit settled', 'win the lawsuit', 'wins the lawsuit', 'defamation settlement', 'defamation verdict'], 0.45, 'divorce'),
    (['impeach', 'impeachment', 'removed from office'], 0.15, 'impeachment'),
    (['25th amendment', 'invoke the 25th', 'invoked the 25th', 'section 4 of the 25th', '25th amendment invocation'], 0.05, '25th Amendment'),
    (['lawsuit', 'settlement', 'settle the lawsuit', 'class action', 'reaches settlement'], 0.4, 'lawsuit settlement'),
    (['face trial', 'stand trial', 'go to trial', 'faces trial', 'stands trial', 'goes to trial', 'brought to trial', 'criminal trial'], 0.35, 'criminal trial'),
    (['be fined by', 'get fined by', 'receive a fine', 'pay a fine', 'fined for', 'eu fine', 'regulatory fine', 'antitrust fine'], 0.4, 'regulatory fine'),
    (['cyberattack', 'cyber attack', 'cyber breach', 'data breach', 'data leak', 'data theft', 'ransomware attack', 'hack the infrastructure', 'critical infrastructure attack', 'attack on the grid', 'government data', 'hacked by', 'hack into', 'ransomware'], 0.35, 'cyberattack'),
    (['be arrested', 'get arrested', 'was arrested', 'been arrested', 'arrested for', 'arrested by', 'taken into custody', 'arraigned', 'in custody'], 0.3, 'arrest'),
    (['be extradited', 'extradited to', 'extradition of', 'extradition request', 'extradited from'], 0.35, 'extradition'),
    (['testify before congress', 'testify before the senate', 'testify before the house', 'testify before a', 'congressional testimony', 'appear before congress', 'appear before the senate', 'appear before the house', 'appear before a', 'senate hearing', 'house hearing', 'committee testimony', 'congressional committee'], 0.5, 'congressional testimony'),
    (['be fired', 'get fired', 'was fired', 'been fired', 'gets fired', ' fired ', 'dismissed', 'terminated', 'be removed', 'get removed', 'was removed', 'been removed', 'removed from his position', 'removed from her position', 'removed from the position', 'removed from his role', 'removed from her role', 'removed from the role', 'removed from his post', 'removed from her post', 'removed from the post', 'ousted from', 'pushed out of'], 0.25, 'employment termination'),
    (['mass layoffs', 'announce layoffs', 'planned layoffs', 'workforce reduction', 'headcount reduction', 'job cuts', 'lay off workers', 'lay off employees', 'cut its workforce', 'reduce its workforce', 'reduce headcount'], 0.35, 'mass layoffs'),
    (['avoid a shutdown', 'avert a shutdown', 'prevent a shutdown', 'avoid the shutdown', 'avert the shutdown', 'end the shutdown', 'shutdown ends', 'shutdown end', 'reopen the government', 'resolve the shutdown'], 0.85, 'government shutdown avoided'),
    (['government shutdown', 'partial shutdown', 'federal shutdown', 'shutdown begins', 'shutdown starts'], 0.15, 'government shutdown'),
    (['raise the debt ceiling', 'lift the debt ceiling', 'suspend the debt limit', 'debt limit be suspended', 'debt limit suspended', 'increase the debt limit', 'raise the debt limit', 'debt ceiling deal', 'debt ceiling agreement', 'resolve the debt limit'], 0.7, 'debt ceiling resolution'),
    (['debt ceiling', 'debt limit', 'hit the debt ceiling', 'breach the debt limit', 'x-date'], 0.65, 'debt ceiling'),
    (['antitrust', 'ftc block', 'doj block', 'block the merger', 'block the acquisition', 'reject the merger', 'challenge the merger', 'challenge the acquisition'], 0.4, 'antitrust action'),
    (['north korea missile', 'north korea nuclear', 'north korea test', 'north korea launch', 'north korea conduct', 'dprk missile', 'dprk nuclear', 'dprk test', 'dprk launch', 'dprk conduct', 'dprk provoc', ' dprk ', 'dprk'], 0.4, 'North Korea provocation'),
    (['water crisis', 'water shortage', 'drought conditions', 'water scarcity', 'reservoir levels', 'water supply falls', 'severe drought', 'exceptional drought', 'lake mead', 'water restrictions', 'drought emergency', 'drought declaration'], 0.3, 'water crisis'),
    (['will it rain', 'chance of rain', 'precipitation'], 0.4, 'weather'),
    # Split out of the generic "hurricane" rule 2026-08-02
    # (hurricane-recalibration): sampled all 29 real matches and found the
    # generic 0.45 rate was never actually exercised by a genuine one-off
    # "will this hurricane happen" question -- every match was one of these
    # two structurally distinct sub-patterns. Must come BEFORE the generic
    # "hurricane" rule below so they intercept first.
    #
    # Category ladder: "Will [Storm] become a Category N hurricane?" -- same
    # ladder artifact as price-threshold/production-delivery-milestone (many
    # rungs for one storm's peak intensity resolve together: if it reaches
    # Cat 3, Cat 1 and Cat 2 also resolve YES). All 5 matches traced to one
    # ticker (KXHURCAT-26BERTHA), 0/5 YES. Unlike price-threshold/delivery-
    # milestone, these titles still contain the bare word "hurricane" so they
    # can't be excluded by simply omitting a keyword -- the generic rule below
    # would still catch them. Intercepted with its own low flat rate instead
    # of true removal; 0/5 is too small a sample to justify literal 0.0, so
    # 0.05 (same order of magnitude as other rare-tail-outcome rates
    # elsewhere in this table).
    (['become a category'], 0.05, 'hurricane category ladder'),
    #
    # First named storm: "Will [Name] be the first named hurricane in
    # [Basin] in [Year]?" -- a many-way field over a fixed, published list of
    # ~24 candidate names (NOAA's annual naming list), structurally similar
    # to win-catchall/competition-award-ranking. All 24 matches traced to one
    # ticker (KXFIRSTHURRICANE-26DEC01EPAC), 1 YES -- consistent with 1/~24.
    (['first named hurricane', 'first named storm'], 0.04, 'first named storm'),
    (['hurricane', 'tropical storm', 'tropical cyclone', 'category 4', 'category 5'], 0.45, 'hurricane'),
    (['earthquake', 'magnitude'], 0.3, 'earthquake'),
    (['volcanic eruption', 'volcano erupts', 'eruption of', 'yellowstone', 'supervolcano', 'volcanic event', 'lava flow', 'pyroclastic'], 0.05, 'volcanic eruption'),
    (['wildfire', 'wildfires', 'wildfire burns', 'wildfire destroys', 'acres burned', 'acres scorched', 'million acres burned', 'wildfire season', 'fire weather', 'fire danger'], 0.35, 'wildfire'),
    (['rate cut', 'rate hike', 'interest rate cut', 'interest rate hike', 'raise rates', 'raise interest rates', 'lower rates', 'lower interest rates', 'cut rates', 'hike rates', 'fomc', 'fed funds rate', 'pause rates', 'hold rates', 'maintain rates', 'rates unchanged', 'rate pause', 'rate hold', 'rates on hold', 'interest rates rise', 'interest rates fall', 'interest rates exceed', 'interest rates drop', 'interest rates above', 'interest rates below', 'rates rise above', 'rates fall below', 'rates exceed'], 0.5, 'Fed rate decision'),
    (['opec', 'opec+', 'opec cut', 'opec increase', 'opec production', 'oil production cut', 'oil production hike', 'oil production increase', 'oil production quota', 'opec meeting', 'opec decision', 'saudi aramco production', 'opec+ alliance', 'reduce oil production', 'increase oil production', 'oil cartel', 'opec agreement', 'opec deal', 'production quota', 'production ceiling'], 0.4, 'OPEC production decision'),
    (['chip export', 'semiconductor export', 'chip ban', 'export restriction on chips', 'export ban on semiconductors', 'chip restriction', 'semiconductor restriction', 'advanced chip', 'export control on ai chips', 'chip to china', 'semiconductor to china', 'entity list chips', 'export license for chips', 'nvidia chip export', 'chip embargo', 'semiconductor embargo', 'advanced computing export', 'export ban list', 'export control list', 'chip export ban', 'add to the entity list'], 0.45, 'chip export restriction'),
    (['quantitative easing', 'quantitative tightening', 'qe program', 'qt program', 'asset purchase program', 'balance sheet expansion', 'balance sheet reduction', 'end qe', 'begin qe', 'restart qe', 'end qt', 'begin qt', 'resume qe', 'purchase treasuries', 'purchase mortgage-backed'], 0.4, 'central bank balance sheet'),
    (['supply chain', 'port strike', 'port congestion', 'shipping delay', 'shipping backlog', 'freight disruption', 'container shortage', 'supply chain crisis', 'logistics crisis', 'port closure', 'supply chain disruption', 'supply chain bottleneck', 'shipping blockage', 'supply disruption', 'freight crisis'], 0.3, 'supply chain disruption'),
    (['recession', 'in recession', 'enters recession'], 0.25, 'recession'),
    (['housing market crash', 'housing crash', 'real estate crash', 'housing market collapse', 'real estate collapse', 'housing bubble burst', 'housing bubble pop', 'home prices crash', 'home prices collapse'], 0.15, 'housing market crash'),
    (['housing prices', 'home prices', 'home values', 'real estate prices', 'median home price', 'housing market see', 'housing market price', 'housing price appreciation', 'price appreciation in the housing', 'house price', 'house prices'], 0.5, 'housing price level'),
    (['default', 'debt default', 'sovereign default'], 0.1, 'sovereign/corporate default'),
    (['trade deficit', 'trade surplus', 'trade balance', 'current account deficit', 'balance of trade'], 0.5, 'trade balance'),
    (['treasury yield', '10-year yield', 'bond yield', 'yield on the', '10-year treasury', '2-year treasury', '30-year treasury', 'yield curve', 'bund yield', 'gilt yield'], 0.5, 'bond yield level'),
    (['housing permits', 'housing starts', 'building permits', 'new home permits', 'residential permits', 'home starts', 'new construction permits', 'housing completions', 'housing units started', 'housing units permitted', 'residential starts'], 0.5, 'housing permits data'),
    (['unemployment rate', 'unemployment', 'jobless rate', 'nonfarm payroll', 'jobs report', 'labor market', 'labor force'], 0.5, 'employment data'),
    (['retail sales', 'consumer spending', 'consumer confidence', 'consumer sentiment', 'personal spending', 'personal consumption', 'durable goods', 'factory orders', 'industrial production'], 0.45, 'economic data release'),
    (['inflation exceed', 'inflation above', 'inflation stay above', 'inflation remain above', 'inflation reach', 'inflation drops below', 'inflation falls below', 'inflation returns to', 'inflation target', 'inflation stays below', 'above the inflation', 'below the inflation'], 0.5, 'inflation threshold'),
    (['inflation rate', 'cpi', 'pce', 'consumer price index', 'core inflation', 'consumer price inflation', 'price inflation', 'inflation reading'], 0.5, 'CPI/inflation data'),
    (['gdp growth', 'gdp contraction', 'gdp shrinks', 'gdp exceed', 'gdp above', 'gdp below', 'gdp surpass', 'economic growth', 'economic contraction', 'grow at', 'growth rate', 'growth of'], 0.5, 'GDP data'),
    (['national debt exceed', 'national debt surpass', 'national debt above', 'national debt reach', 'federal debt exceed', 'federal debt surpass', 'federal debt above', 'federal debt reach', 'us debt exceed', 'us debt surpass', 'debt-to-gdp'], 0.5, 'national debt threshold'),
    (['s&p 500 above', 's&p 500 below', 's&p 500 exceed', 's&p 500 reach', 's&p above', 's&p below', 'dow jones above', 'dow jones below', 'nasdaq above', 'nasdaq below', 'nasdaq exceed', 'vix above', 'vix below', 'sp500 above', 's&p500 above', 's&p500 below', 'russell 2000 above', 'russell 2000 below'], 0.5, 'equity index level'),
    (['beat earnings', 'beats earnings', 'beat analyst', 'beat analysts', 'miss earnings', 'misses earnings', 'earnings beat', 'earnings miss', 'beat on earnings', 'earnings per share above', 'eps above', 'eps beat', 'earnings surprise', 'earnings estimate'], 0.5, 'earnings beat/miss'),
    (['pdufa', 'pdufa date', 'pdufa target date'], 0.85, 'PDUFA date'),
    (['clinical hold', 'clinical hold lifted', 'fda clinical hold', 'partial clinical hold'], 0.1, 'FDA clinical hold'),
    (['complete response letter', 'crl issued', 'received a crl', 'resubmission', 'resubmitted to the fda', 'respond to the crl', 'address the crl'], 0.6, 'FDA complete response letter'),
    (['advisory committee', 'fda advisory', 'adcom', 'fda panel', 'fda panel vote', 'fda panel meeting', 'advisory panel', 'fda advisory committee'], 0.5, 'FDA advisory committee'),
    (['fda approve', 'fda approval', 'fda approves', 'fda cleared', 'fda authorization', 'fda authorize', 'fda clears'], 0.4, 'FDA approval'),
    (['sec approve', 'sec approves', 'sec approval', 'fcc approve', 'fcc approves', 'fcc approval', 'ferc approve', 'ferc approves', 'ferc approval', 'regulatory approval', 'regulatory clearance', 'cfpb approve', 'ftc approve', 'epa approve'], 0.4, 'regulatory approval'),
    (['network upgrade', 'protocol upgrade', 'hard fork', 'soft fork', 'mainnet upgrade', 'consensus upgrade', 'ethereum upgrade', 'eth upgrade', 'bitcoin upgrade', 'taproot upgrade', 'blockchain upgrade', 'chain upgrade', 'protocol migration', 'pectra', 'shapella', 'eip-', 'bip-', 'scheduled upgrade'], 0.65, 'crypto protocol upgrade'),
    (['bitcoin', 'btc price', 'btc above', 'btc below', 'ethereum', 'eth price', 'eth above', 'eth below', 'crypto', 'cryptocurrency'], 0.5, 'crypto price level'),
    (['bitcoin etf', 'crypto etf', 'ethereum etf', 'spot etf', 'etf approval'], 0.5, 'crypto ETF'),
    (['gold price', 'gold prices', 'gold above', 'gold below', 'gold exceed', 'gold surpass', 'price of gold', 'gold reaches', 'gold reach', 'gold hit', 'gold top', 'crude oil', 'oil price', 'oil prices', 'oil above', 'oil below', 'brent crude', 'wti crude', 'price of oil', 'barrel of oil', 'natural gas price', 'natural gas above', 'natural gas below', 'silver price', 'copper price', 'energy price', 'energy prices', 'commodity price', 'commodity prices'], 0.4, 'commodity price level'),
    # Removed 2026-08-02 (price-threshold-recalibration): the flat 0.35 rate
    # was badly miscalibrated (real YES rate 82-100%), but NOT because the
    # rate was wrong in general -- root cause was corpus composition. Real
    # Kalshi tickers were traced (event_ticker grouping): matches were
    # dominated by MAX/MIN "range" ladder series (KXWTIMAX, KXH100MAX, etc.),
    # where many threshold rungs for the SAME underlying asset/date all
    # resolve YES together because the question is "did the running max/min
    # ever touch this level," not a standalone one-off price call. Raising
    # the flat rate to match this corpus would encode that structural
    # artifact as if it were a general truth -- a standalone "will Bitcoin
    # exceed $200k" question is not 82% likely just because it mentions a
    # threshold. No single flat number is honest here; removed rather than
    # mis-set, so these titles get no heuristic base_rate (Claude scores
    # them directly) instead of an edge computed against a fabricated prior.
    (['secondary offering', 'follow-on offering', 'equity offering', 'share issuance', 'stock issuance', 'at-the-market offering', 'atm offering', 'registered direct offering', 'shelf offering', 'shelf registration', 'capital raise', 'equity raise'], 0.35, 'secondary equity offering'),
    (['announce an ipo', 'officially announce an ipo', 'ipo announcement', 'going public', 'go public'], 0.25, 'IPO announcement'),
    (['ipo by', 'ipo before', 'initial public offering'], 0.3, 'IPO timing'),
    (['make his mlb debut', 'make her mlb debut', 'play in a game for', 'called up', 'nhl debut', 'nba debut', 'make his debut', 'make her debut'], 0.35, 'sports debut'),
    (['close the merger', 'close the acquisition', 'close the deal', 'complete the merger', 'complete the acquisition', 'finalize the acquisition', 'finalize the merger', 'merger be completed', 'acquisition be completed', 'merger to close', 'acquisition to close', 'deal to close', 'close the buyout', 'transaction close', 'deal close by', 'acquisition close', 'merger close'], 0.8, 'merger close (signed deal)'),
    (['hostile takeover', 'unsolicited offer', 'unsolicited bid', 'hostile bid', 'reject the takeover', 'poison pill', 'tender offer'], 0.42, 'hostile takeover bid'),
    (['merger', 'acquisition', 'acquired by', 'be acquired', 'get acquired', 'was acquired', 'acquire', 'take private', 'buyout', 'takeover', 'be taken over', 'be bought out'], 0.35, 'merger or acquisition'),
    (['enter the market', 'enter the healthcare', 'enter the insurance', 'enter the banking', 'enter the auto', 'enter the space', 'launch a new business', 'expand into', 'new market entry', 'move into the'], 0.35, 'corporate market entry'),
    (['ev sales', 'electric vehicle sales', 'ev market share', 'electric vehicle market share', 'ev adoption', 'ev penetration', 'electric car sales', 'battery electric vehicle sales', 'bev sales', 'electric vehicle adoption', 'ev registrations', 'ev market penetration', 'electric vehicle market penetration'], 0.45, 'EV adoption milestone'),
    # Removed 2026-08-02 (production-delivery-milestone-recalibration): same
    # root cause as price-threshold-recalibration. All 39 matched settled
    # markets traced to exactly 2 event_tickers (KXTSLA-26JULDELIV,
    # KXBA-26JULDELIV) -- delivery-count ladders ("report above N deliveries")
    # where many threshold rungs for the same underlying quarterly number
    # resolve together. A flat rate can't be honest for a ladder; removed
    # rather than mis-set, same reasoning as price-threshold-recalibration.
    (['be sold by', 'forced to sell', 'forced sale', 'divest', 'divestiture', 'forced divestiture', 'sell off', 'spin off its', 'spin out', 'spin off'], 0.35, 'corporate divestiture'),
    (['issue bonds', 'bond issuance', 'bond offering', 'bond sale', 'bond auction', 'treasury auction', 'debt offering', 'sovereign bond', 'issue new bonds', 'bond deal', 'bond placement', 'bond raise', 'issue debt', 'complete a bond', 'price a bond'], 0.65, 'bond/debt issuance'),
    (['stock split', 'share split', 'reverse stock split', 'forward stock split', 'split its stock', 'announce a split'], 0.2, 'stock split'),
    (['municipal bankruptcy', 'city bankruptcy', 'county bankruptcy', 'municipality default', 'city default', 'municipal default', 'file for chapter 9', 'chapter 9 bankruptcy', 'city insolvency', 'detroit bankruptcy', 'city declares bankruptcy', 'city declare insolvency', 'municipality insolvency', 'municipal insolvency', 'city declare bankruptcy'], 0.1, 'municipal bankruptcy'),
    (['bankruptcy', 'file for bankruptcy', 'goes bankrupt', 'go bankrupt', 'declare bankruptcy', 'seek bankruptcy'], 0.15, 'corporate bankruptcy'),
    (['announce a partnership', 'announce a deal with', 'sign a partnership', 'sign a deal with', 'enter a partnership', 'enter into a partnership', 'licensing agreement with', 'licensing deal with', 'partnership with', 'supply agreement with', 'supply deal with', 'commercial agreement with', 'joint venture with', 'collaboration agreement', 'strategic partnership', 'strategic alliance', 'distribution deal', 'distribution agreement', 'licensing agreement'], 0.35, 'corporate partnership'),
    (['added to the s&p', 'added to the s&p 500', 'join the s&p 500', 'included in the s&p', 'included in the s&p 500', 's&p 500 inclusion', 's&p 500 addition', 'added to s&p 500', 's&p index inclusion', 'added to the index', 'dow jones inclusion', 'nasdaq 100 inclusion', 'russell 1000 addition', 'russell 2000 addition', 'enter the s&p', 'enter the dow'], 0.5, 'index inclusion'),
    (['will attend', 'will appear at', 'will speak at', 'will be present at', 'appear at the summit', 'appear at the conference', 'appear at the nato', 'appear at the g7', 'appear at the g20', 'appear at davos', 'appear at the un', 'attend the summit', 'attend the conference', 'attend the g7', 'attend the g20', 'attend the nato', 'attend the un general assembly', 'attend the davos', 'attend the world economic forum', 'attend the apec', 'attend the cop', 'attend the wef'], 0.65, 'event attendance'),
    (['open a new factory', 'open a new plant', 'open a new facility', 'announce a new factory', 'announce a factory', 'announce a plant', 'build a factory in', 'build a plant in', 'build a facility in', 'establish a factory', 'open its factory', 'new headquarters', 'new campus', 'open a data center', 'build a data center', 'announce a data center', 'open a fulfillment center', 'announce a fulfillment center'], 0.4, 'facility announcement'),
    (['stock buyback', 'share buyback', 'share repurchase', 'buyback program', 'repurchase program', 'buyback announcement', 'announce a buyback', 'announce a repurchase', 'repurchase shares', 'repurchase its shares', 'buyback plan', 'buyback authorization'], 0.4, 'share buyback'),
    (['dividend increase', 'increase its dividend', 'raise its dividend', 'dividend cut', 'cut its dividend', 'suspend its dividend', 'dividend announcement', 'declare a dividend', 'special dividend', 'dividend payment', 'announce a dividend'], 0.4, 'dividend announcement'),
    (['credit rating downgrade', 'credit downgrade', 'downgrade to junk', 'rating downgrade', 'credit rating cut', "moody's downgrade", 's&p downgrade', 'fitch downgrade', 'credit rating upgrade', 'rating upgrade', 'credit upgrade', 'upgrades credit rating', 'downgraded by moody', 'downgraded by s&p', 'downgraded by fitch', 'upgraded by moody', 'upgraded by s&p', 'upgraded by fitch', 'sovereign downgrade', 'debt downgrade', 'junk status'], 0.4, 'credit rating change'),
    (['short seller report', 'short seller', 'hindenburg research', 'citron research', 'muddy waters', 'short report on', 'short thesis', 'short attack', 'fraud allegations', 'accounting fraud allegations'], 0.3, 'short seller report'),
    (['bank failure', 'bank collapse', 'banking crisis', 'bank run', 'bank bailout', 'bank insolvency', 'financial institution fail', 'savings and loan'], 0.15, 'bank failure'),
    (['filibuster reform', 'end the filibuster', 'eliminate the filibuster', 'abolish the filibuster', 'filibuster rule', 'nuclear option', 'carve out the filibuster', 'filibuster carve-out', 'senate rules change', 'senate procedural reform', '60-vote threshold', 'simple majority in the senate'], 0.1, 'filibuster reform'),
    (['gun control', 'gun legislation', 'firearms legislation', 'assault weapons ban', 'red flag law', 'background check legislation', 'firearms restriction', 'gun safety legislation', 'ban assault weapons', 'gun law', 'gun reform'], 0.2, 'gun legislation'),
    (['exchange rate', 'currency exchange', 'depreciate', 'depreciation', 'appreciate against', 'appreciate versus', 'currency falls', 'peso depreciate', 'euro falls', 'yen depreciate', 'dollar strengthen', 'dollar weaken', 'dollar index'], 0.4, 'exchange rate'),
    (['be valued at', 'be valued above', 'valued above', 'be worth', 'worth more than', 'market cap above', 'market cap exceed', 'market cap of', 'valuation of', 'valuation above', 'valued at $', 'valuation at $'], 0.35, 'company valuation'),
    (['surpass github', 'surpass google', 'surpass microsoft', 'surpass apple', 'surpass amazon', 'surpass meta', 'market share above', 'market share exceed', 'beat google', 'beat microsoft', 'beat apple'], 0.35, 'tech company milestone'),
    (['age restriction', 'age verification', 'age limit for social media', 'restrict social media', 'restricted for minors', 'restricted to minors', 'social media age', 'minors on social media', 'age gate', 'online age verification', 'social media for minors', 'ban for minors', 'minors from social media'], 0.3, 'age restriction legislation'),
    (['remain ceo', 'remain as ceo', 'stay as ceo', 'continue as ceo', 'keep his job as', 'keep her job as', 'retain his position', 'retain her position', 'remain in office', 'remain in power', 'stay in power', 'stay in office', 'stay on as'], 0.65, 'CEO retention'),
    (['tiktok ban', 'ban tiktok', 'tiktok be banned', 'ban on tiktok', 'social media ban', 'tech ban', 'platform ban', 'block tiktok', 'ban chinese apps'], 0.2, 'tech platform ban'),
    (['invoke article 5', 'article 5 of nato', 'article 5 of the nato', 'article 5 be invoked', "invoke nato's article 5", "nato's collective defense", 'collective defense clause', 'mutual defense clause', 'article v of the nato'], 0.05, 'NATO Article 5'),
    (['recapture', 'retake', 'reclaim territory', 'liberate', 'advance on', 'military offensive', 'push into', 'counteroffensive', 'seize territory', 'capture the city', 'take back', 'overrun'], 0.3, 'military offensive'),
    (['referendum on independence', 'independence referendum', 'vote on independence', 'vote on secession', 'self-determination vote', 'plebiscite on', 'hold a referendum', 'independence vote'], 0.15, 'independence referendum'),
    (['troop withdrawal', 'withdraw troops', 'pull out troops', 'military withdrawal', 'military drawdown', 'drawdown of troops', 'troops leave', 'forces leave', 'exit afghanistan', 'end the mission', 'end combat operations', 'remove troops from', 'troops return home'], 0.3, 'military withdrawal'),
    (['civil war', 'armed conflict', 'armed uprising', 'insurgency', 'rebel forces', 'sectarian conflict', 'internal conflict', 'internal war', 'militias', 'warlord'], 0.25, 'civil conflict'),
    (['declare war', 'invade', 'military strike', 'launch attack'], 0.15, 'military conflict'),
    (['coup', 'overthrow', 'regime change'], 0.1, 'political coup'),
    (['legalize cannabis', 'legalize marijuana', 'legalize recreational', 'marijuana legalization', 'cannabis legalization', 'legalize gambling', 'legalize sports gambling', 'sports gambling', 'gambling legalization', 'gambling legislation', 'legalize drugs', 'drug legalization', 'decriminalize marijuana', 'decriminalize cannabis', 'pass cannabis legislation', 'recreational marijuana bill', 'recreational cannabis bill', 'gambling bill', 'sports betting', 'online gambling', 'legal gambling', 'legal cannabis', 'legal marijuana', 'gambling'], 0.3, 'legalization'),
    (['be cancelled', 'be canceled', 'be postponed', 'gets cancelled', 'gets canceled', 'gets postponed', 'cancel the event', 'postpone the event', 'cancel the summit', 'cancel the conference', 'cancel the olympics', 'cancel the world cup', 'cancel the games', 'call off the', 'called off'], 0.1, 'event cancellation'),
    (['break the record', 'breaks the record', 'set a new record', 'sets a new record', 'beat the record', 'shatter the record', 'world record', 'all-time record in', 'record-breaking performance', 'olympic record', 'personal record'], 0.3, 'athletic record'),
    (['wealth tax', 'wealth levy', 'tax on wealth', 'billionaire tax', 'millionaire tax', 'ultra-rich tax', 'tax on assets', 'net worth tax', 'wealth surtax', 'mega-wealthy tax'], 0.15, 'wealth tax'),
    (['product recall', 'drug recall', 'safety recall', 'fda recall', 'voluntary recall', 'issue a recall', 'announce a recall', 'pull the product', 'withdraw the drug', 'market withdrawal', 'device recall'], 0.25, 'product/drug recall'),
    (['nobel prize', 'nobel peace prize', 'nobel laureate', 'win the nobel', 'receive the nobel'], 0.1, 'Nobel Prize'),
    (['pulitzer prize', 'pulitzer', 'win the pulitzer'], 0.1, 'Pulitzer Prize'),
    (['grammy', 'oscar', 'academy award', "palme d'or", 'emmy award', 'golden globe award', 'tony award', 'bafta award', 'sag award', 'screen actors guild', 'sundance award'], 0.2, 'entertainment award'),
    (['renewable energy', 'solar energy supply', 'wind energy supply', 'clean energy supply', 'electricity from renewables', 'green energy percentage', 'renewables share', 'renewable electricity', 'clean electricity'], 0.4, 'renewable energy'),
    (['political scandal', 'sex scandal', 'financial scandal', 'corruption scandal', 'bribery scandal', 'abuse of power', 'misconduct scandal', 'cover-up', 'whistleblower alleges', 'kickback scheme'], 0.45, 'political scandal'),
    (['housing correction', 'home price correction', 'real estate correction', 'housing market correct', 'home prices correct', 'prices correct', 'housing downturn', 'housing slowdown', 'price correction', 'market correction'], 0.2, 'housing price correction'),
    (['autonomous vehicle', 'self-driving car', 'robotaxi', 'full self-driving', 'autonomous taxi', 'level 4 autonomy', 'level 5 autonomy', 'fully autonomous driving', 'driverless car', 'driverless vehicle', 'self-driving'], 0.25, 'autonomous vehicle deployment'),
    (['quantum computing', 'quantum supremacy', 'quantum advantage', 'break encryption', 'quantum computer', 'quantum error correction', 'fault-tolerant quantum'], 0.1, 'quantum computing'),
    (['mars mission', 'mission to mars', 'manned mars', 'crewed mars', 'human mission to mars', 'mars landing', 'mars orbit', 'mars colony', 'deep space mission', 'interplanetary'], 0.15, 'deep space mission'),
    (['tweet about', 'tweet on', 'tweets about', 'tweets on', 'post about', 'post on twitter', 'post on x ', 'post on instagram', 'mention on twitter', 'mention on x', 'mention on social', 'post to twitter', 'post to x ', 'share on twitter', 'share on x ', 'elon tweet', 'trump tweet', 'post to social media', 'social media post about', 'on twitter about', 'on x about', 'twitter post about', 'x post about'], 0.75, 'social media post'),
    (['concert tour', 'world tour', 'go on tour', 'announce a tour', 'headlining tour', 'headline tour', 'headline a tour', 'north american tour', 'european tour', 'stadium tour', 'arena tour', 'announce tour dates', 'tour dates announced'], 0.45, 'concert tour'),
    (['new iphone', 'iphone 17', 'iphone 18', 'iphone 19', 'iphone 20', 'new ipad', 'new mac', 'new macbook', 'new apple', 'apple announces', 'apple reveal', 'samsung galaxy', 'new galaxy', 'galaxy s', 'galaxy flagship', 'pixel phone', 'new pixel', 'ar glasses', 'ar headset', 'vr headset', 'smart glasses', 'mixed reality headset', 'vision pro', 'next-gen headset', 'product announcement', 'product reveal', 'announce a new iphone', 'announce a new mac', 'announce a new samsung', 'announce a new pixel'], 0.55, 'tech product announcement'),
    (['premieres', 'premiere by', 'movie release', 'film release', 'tv show', 'television show', 'new season', 'season finale', 'season premiere', 'sequel', 'spin-off', 'box office', 'streaming', 'in theaters', 'in cinemas', 'music video', 'album drops', 'album release', 'official trailer', 'teaser trailer', 'trailer release', 'trailer for', 'episode', 'documentary'], 0.25, 'media/entertainment release'),
    # Was 0.25 labeled "show renewal" until analysis/heuristic_backtest.py
    # (2026-08-02) found it's not really about renewal at all: "season N" and
    # bare "movie"/"film" mostly catch many-way fields -- individual reality-
    # competition entrants ("Will X win Top Chef Season 23?") and individual
    # Emmy/award nominees ("Will X win Movie/Limited Actor at the Emmys?"),
    # the same failure mode as the " win " catch-all (see win-catchall-recalibration
    # above), just not always containing the literal word "win" (e.g. "finish
    # 3rd place"). Measured actual YES rate: 1.73% across 637 settled markets.
    (['season 2', 'season 3', 'season 4', 'season 5', 'season 6', 'season 7', 'season 8', 'season 9', 'movie', 'film'], 0.02, 'competition/award ranking'),
    (['starship', 'falcon heavy', 'falcon 9', 'spacex launch', 'rocket launch'], 0.4, 'SpaceX launch'),
    (['nasa', 'moon landing', 'lunar gateway', 'artemis', 'space station', ' iss ', 'james webb', 'land on the moon', 'land astronauts on the moon', 'crewed lunar', 'lunar lander', 'lunar module'], 0.3, 'NASA mission'),
    (['phase 3', 'clinical trial', 'phase 2', 'drug trial', 'clinical study'], 0.35, 'clinical trial'),
    (['pandemic', 'epidemic', 'outbreak', 'public health emergency'], 0.25, 'pandemic/epidemic'),
    (['variant of concern', 'covid variant', 'new variant', 'sars-cov', 'covid strain', 'virus variant', 'declare a public health emergency', 'mpox', 'monkeypox'], 0.3, 'COVID variant'),
    (['die before', 'die by', 'pass away before', 'pass away by', 'survive until', 'still alive by', 'alive by', 'death before', 'death by date'], 0.15, 'health/mortality'),
    (['hottest year', 'warmest year', 'record temperature', 'temperature record', 'record heat', 'record warming', 'coldest year', 'coldest winter', 'record cold', 'climate record', 'record rainfall', 'record drought', 'record snowfall', 'all-time record'], 0.4, 'climate record'),
    (['carbon tax', 'carbon credit', 'net zero', 'emissions target', 'paris agreement', 'clean energy', 'renewable energy mandate'], 0.35, 'climate/energy policy'),
    (['gpt-5', 'gpt-6', 'gpt 5', 'gpt 6', 'claude 4', 'claude 5', 'claude-4', 'claude-5', 'gemini 2', 'gemini 3', 'gemini ultra', 'llama 4', 'llama-4', 'llm release', 'ai model release', 'release a new model', 'release their next model', 'agi by', 'artificial general intelligence by', 'achieved artificial general intelligence', 'achieve artificial general intelligence', 'claims agi', 'declare agi', 'announce agi', 'claims to have achieved agi'], 0.25, 'AI model release'),
    (['ai pass', 'ai passes', 'ai score', 'ai scores', 'ai outperform', 'ai beat', 'ai beats', 'ai achieve', 'artificial intelligence pass', 'llm pass', 'language model pass', 'ai take the bar', 'ai take the mcat', 'ai pass the', 'machine learning achieve'], 0.4, 'AI capability milestone'),
    (['ai regulation', 'regulate ai', 'ban ai', 'ai ban', 'ai law', 'ai legislation', 'ai governance', 'artificial intelligence regulation', 'autonomous weapons ban', 'lethal autonomous weapons', 'ai safety law', 'ai liability'], 0.3, 'AI regulation'),
    (['tariff on', 'tariffs on', 'tariff rate', 'impose a tariff', 'tariff increase', 'tariff reduction', 'trade war', 'trade deal', 'trade agreement'], 0.4, 'trade tariffs'),
    (['deport', 'deportation', 'mass deportation', 'immigration ban', 'border wall', 'sanctuary city', 'immigration bill', 'immigration legislation', 'immigration reform', 'immigration law', 'immigration policy'], 0.35, 'immigration policy'),
    (['approval rating', 'job approval', 'favorability rating', 'approve of the', 'disapprove of the', 'net approval'], 0.5, 'approval rating'),
    (['union election', 'union vote', 'vote to unionize', 'unionize the', 'labor union vote', 'form a union', 'nlrb election', 'union drive', 'union campaign', 'union organizing', 'right to organize', 'vote on unionization', 'union certification', 'union authorization', 'organize a union', 'unionization vote'], 0.4, 'unionization vote'),
    (['go on strike', 'labor strike', 'workers strike', 'union strike', 'strike action', 'work stoppage', 'walkout', 'general strike', 'nationwide strike', 'national strike', 'transit strike', 'teachers strike', 'nurses strike', 'rail strike', 'airline strike'], 0.3, 'labor strike'),
    # sports-award-recalibration (2026-08): was 0.2, measured 4/107 YES (3.7%) across
    # 4 event_tickers (KXMLBASGMVP-26, KXWNBACCUPMVP-26, KXWCAWARD-26GGLOVE,
    # KXNBASUMMERMVP-2026) -- a many-way field (one winner per award among ~21-36
    # named nominees), same pattern as win-catchall/show-renewal/first-named-storm.
    (['mvp', 'cy young', 'rookie of the year', 'heisman', 'hall of fame', 'all-star', 'golden glove', 'best player'], 0.04, 'sports award'),
    (['make the playoffs', 'reach the playoffs', 'qualify for', 'qualify for the champions league', 'advance to', 'make it to', 'clinch a playoff'], 0.35, 'sports qualification'),
    (['get traded', 'be traded', 'trade deadline', 'sign with', 'free agent signing', 'sign a contract', 'extension'], 0.3, 'sports transaction'),
    (['become ceo', 'be named ceo', 'be appointed ceo', 'new ceo', 'become the ceo', 'named as ceo', 'appoint a new ceo', 'become cfo', 'be named cfo', 'new cfo', 'become chair', 'be named chair', 'become chairman'], 0.35, 'corporate leadership appointment'),
    (['launch', 'launches', 'launched by', 'launches by'], 0.35, 'product launch'),
    ([' win '], 0.08, 'competition win'),
]

def estimate_base_rate(market: dict) -> float | None:
    """
    Simple heuristic pass before calling Claude (saves tokens).
    Returns a float 0.0-1.0 if a known signal applies, else None.
    scorer.py handles None markets with the full Claude call.

    Backed by the single shared _HEURISTIC_RULES table (keywords, rate,
    label) -- get_heuristic_label() reads the exact same table, so the two
    can never drift out of sync again (backlog: heuristic_label-vs-base_rate-desync,
    fixed 2026-08-01: previously two independently-ordered lists, 632 of
    2,344 heuristic-matched settled markets got a rate but no label).
    """
    title = (market.get("title") or "").lower()
    for keywords, rate, _label in _HEURISTIC_RULES:
        if any(k in title for k in keywords):
            return rate
    return None


def get_heuristic_label(market: dict) -> str | None:
    """
    Returns a short human-readable category label for the matched heuristic.
    Used in build_prompt() so Claude sees the category name alongside the base
    rate, enabling it to apply category-specific calibration rules.
    Returns None when estimate_base_rate() would also return None -- both
    read the same _HEURISTIC_RULES table, so this is now structurally
    guaranteed rather than merely intended.
    """
    title = (market.get("title") or "").lower()
    for keywords, _rate, label in _HEURISTIC_RULES:
        if any(k in title for k in keywords):
            return label
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

    # Real Kalshi GetMarketOrderbook shape (confirmed live 2026-07-25):
    # {"orderbook_fp": {"yes_dollars": [[price, size], ...], "no_dollars": [...]}}
    # yes_dollars = resting YES-buy orders (the YES bid book); no_dollars =
    # resting NO-buy orders, which is the YES ask book (accepting a NO bid
    # at price P is equivalent to selling YES at P) -- depth only cares
    # about size per level, not price, so no price inversion is needed.
    ob_fp = orderbook.get("orderbook_fp")
    if isinstance(ob_fp, dict):
        bids = _extract_levels(ob_fp.get("yes_dollars") or [])
        asks = _extract_levels(ob_fp.get("no_dollars") or [])
    else:
        # Older/alternate shape some callers may still pass in directly.
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

    base_rate       = estimate_base_rate(market)
    heuristic_label = get_heuristic_label(market)

    if mid_price is not None and base_rate is not None:
        raw_edge = abs(base_rate - mid_price)
    else:
        raw_edge = None

    # Net-of-spread edge: subtract half the bid-ask spread from raw_edge.
    # Entering at ask (not mid) means you pay half the spread on entry.
    # net_edge < 0 means the spread consumes the entire theoretical edge.
    half_spread = (yes_ask - yes_bid) / 2 if (yes_bid > 0 and yes_ask > 0) else 0
    net_edge = round(raw_edge - half_spread, 6) if raw_edge is not None else None

    # Fee-adjusted edge: subtract Kalshi's per-contract fee from net_edge.
    # Fee is direction-independent — same structure for YES or NO contracts.
    # Converts fee (dollars) to probability units by dividing by unit_size.
    unit_size = config.get("betting", {}).get("unit_size", 10)
    fee_dollars = kalshi_fee(mid_price, unit_size) if mid_price is not None else 0.0
    fee_as_pp = fee_dollars / unit_size if unit_size > 0 else 0.0
    net_edge_after_fee = round(net_edge - fee_as_pp, 6) if net_edge is not None else None

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
        "heuristic_label":     heuristic_label,
        "raw_edge":            raw_edge,
        "net_edge":            net_edge,
        "net_edge_after_fee":  net_edge_after_fee,
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


_HIGH_PRICE_THRESHOLD = 0.85


def apply_high_price_filter(markets: list[dict]) -> tuple[list[dict], int]:
    """
    Removes markets where market_price >= 0.85 (low return potential).
    Returns (kept_markets, filtered_count).
    Logs a [FILTERED] line for each removed market.
    Markets with no mid_price pass through with a warning.
    """
    kept = []
    filtered = 0
    for m in markets:
        mp = m.get("mid_price")
        if mp is None:
            ticker = m.get("ticker", "?")
            print(f"[WARN] {ticker} — market_price missing, passing through high-price filter")
            kept.append(m)
            continue
        if mp >= _HIGH_PRICE_THRESHOLD:
            ticker = m.get("ticker", "?")
            print(f"[FILTERED] {ticker} — market price {mp:.0%} above 0.85 threshold, low return potential")
            filtered += 1
        else:
            kept.append(m)
    return kept, filtered


def score_markets(markets: list[dict], config: dict) -> tuple[list[dict], int]:
    """
    Scores all filtered markets and returns (sorted_markets, high_price_filtered_count).
    Markets at or above the 0.85 high-price threshold are removed before sorting.
    """
    scored = [score_market(m, config) for m in markets]
    scored, hp_filtered = apply_high_price_filter(scored)
    # Sort: watchlist-overlap first, then flagged, then by edge desc
    scored.sort(key=lambda m: (
        not m.get("watchlist_signal", False),
        not m.get("flag", False),
        -(m.get("raw_edge") or 0),
    ))
    return scored, hp_filtered
