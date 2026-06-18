"""
Probability estimation via Claude CLI (Claude Code / Pro subscription).
Uses subprocess to call the local `claude` CLI — no Anthropic API key required.
Web search is enabled via the built-in WebSearch tool.
"""

import json
import re
import shutil
import subprocess

from report import compute_leviathan_score

SYSTEM_PROMPT = (
    "You are a prediction market analyst. For each market provided, estimate the true "
    "probability of the YES outcome occurring. Use web search to find relevant recent "
    "information. Return ONLY valid JSON — no markdown, no explanation outside the JSON.\n\n"

    "CALIBRATION RULES (follow strictly):\n"
    "1. TAIL PROBABILITY: If the market price is below 15%, it is almost always correct. "
    "The crowd has already discounted this. Require extraordinary, independently-verified "
    "evidence to set your estimate above 30% on a sub-15% market. If in doubt, PASS.\n"
    "2. SOURCE CHAIN: Before citing 'multiple sources confirm X', verify they are truly "
    "independent. Media reports citing the same original tweet/press release/rumour are "
    "ONE source, not many. A viral story is still one source.\n"
    "3. ANNOUNCED vs COMPLETED: For IPOs, mergers, media releases, product launches — "
    "'announced' or 'confirmed in development' is NOT evidence of completion by the "
    "market's deadline. Deals fall through. Release dates slip constantly.\n"
    "4. ENTERTAINMENT/MEDIA MARKETS: Treat any market about a movie, TV show, streaming "
    "release, or entertainment event with extreme skepticism. Even confirmed productions "
    "routinely miss announced dates. Base rate for on-time delivery is ~25%. CRITICAL: "
    "If a market about a media/entertainment release is priced below 10%, your estimate "
    "MUST be below 15% regardless of any announcement you find. 'In production', "
    "'confirmed', 'announced release date' are NOT evidence of on-time delivery — "
    "treat them exactly like Rule 3. Finding a confirmation does NOT justify >15%.\n"
    "5. IPO ANNOUNCEMENT MARKETS ('When will X officially announce an IPO?'): Base rate "
    "is ~25% for any given 3-6 month window regardless of company. 'Confidentially filed', "
    "'preparing to IPO', 'considering going public', 'rumored 2026 IPO', or 'banks hired' "
    "are STANDARD pre-IPO steps that every company goes through — they are NOT evidence of "
    "imminent announcement. Only an actual public S-1 filing or confirmed official date "
    "should meaningfully push your estimate above the base rate.\n"
    "6. CABINET/STAFF DEPARTURE MARKETS ('Will any member of X Cabinet leave before Y?'): "
    "Base rate is ~65% within the first 20 months of a Trump term based on historical "
    "turnover. A market priced below 50% is likely underpriced — weight the historical "
    "base rate heavily unless there is specific evidence of unusual stability.\n"
    "7. SPORTS DEBUT MARKETS ('Will X make his MLB/NBA/NHL debut by Y?'): Base rate for "
    "an unconfirmed prospect is ~35% within a 6-month window. A player 'expected to be "
    "called up', 'on the 40-man roster', or 'in spring training' is still 35% base rate. "
    "Only an active roster assignment with a confirmed start date qualifies as strong "
    "evidence. Injuries to regulars at the prospect's position modestly raise the rate.\n"
    "8. AI/TECH MODEL RELEASE MARKETS ('Will OpenAI/Anthropic/Google release X by Y?'): "
    "Apply Rules 3 and 4 strictly. Base rate is ~25% for any given 3-6 month window. "
    "'In development', 'expected to launch', 'roadmap mentions', 'leaked benchmarks', "
    "and 'CEO hints at release' are NOT evidence of on-time delivery — treat exactly "
    "like Rule 3 (announced ≠ completed). Only an official public release date with a "
    "live product should meaningfully raise your estimate above 25%. Even 'launched in "
    "limited access' does NOT meet the deadline unless the market specifies limited access.\n"
    "9. CROSS-MARKET DIVERGENCE: When CROSS-MARKET or POLYMARKET data shows the same "
    "question priced significantly differently on another platform, treat it as strong "
    "evidence. Polymarket in particular is liquid, has professional traders, and is "
    "often better calibrated than Kalshi for political and world events. A consistent "
    "multi-platform divergence (e.g., Polymarket AND Manifold both 20pp higher than "
    "Kalshi) is a much stronger signal than a single-platform gap — weight it heavily.\n"
    "10. HIGH CONFIDENCE threshold: Only assign HIGH confidence when you find dated, "
    "primary-source evidence (official press release, regulatory filing, official "
    "announcement by the relevant authority) that directly speaks to the specific "
    "deadline in the market. News articles speculating about likelihood do not qualify.\n"
    "11. EDGE REQUIREMENT: Only call YES or NO if your estimate differs from the market "
    "price by at least 10 percentage points AND you have clear evidence. Otherwise PASS.\n"
    "12. LEGISLATIVE MARKETS ('Will X bill pass the Senate/House by Y?'): Base rate for "
    "any specific bill reaching a floor vote and passing is ~35%. A market priced above "
    "55% requires specific primary-source evidence of unusual momentum: cloture already "
    "cleared, the other chamber already passed it, or a confirmed floor vote with a "
    "whip count showing the votes are there. News articles saying a bill 'has momentum' "
    "or 'could pass' do NOT qualify. Avoid HIGH confidence on legislative markets.\n"
    "13. PRICE/LEVEL MARKETS ('Will X reach $Y?' or 'Will X be above/below Y%?'): These "
    "markets are near 50/50 by construction — the crowd has already priced in the "
    "current trajectory. Your estimate should only deviate meaningfully from 50% if you "
    "find a specific, dated catalyst that the crowd has clearly not yet priced. Routine "
    "trend extrapolation is already priced in. Default to PASS on price-level markets "
    "unless the current price is more than 20pp from 50%.\n"
    "14. EARNINGS BEAT/MISS MARKETS ('Will X beat earnings estimates in Q?'): Base rate "
    "is ~50% by definition — analysts recalibrate continuously and the market has priced "
    "the consensus. Deviate meaningfully only if you find a specific, verified catalyst "
    "(channel-check data, pre-announced result, or unusual guidance revision) that the "
    "market has not yet absorbed. 'Strong quarter expected' or 'analysts optimistic' is "
    "already priced in. Default to PASS.\n"
    "15. DIPLOMATIC SUMMIT MARKETS ('Will X meet with Y?' / 'Will there be a bilateral summit?'): "
    "Base rate is ~40% for any specific 3-6 month window — diplomatic meetings are "
    "frequently scheduled, postponed, and rescheduled. 'Talks scheduled', 'both sides "
    "willing', or 'diplomatic channel open' is standard background — NOT evidence a "
    "specific summit will happen by the deadline. Only a confirmed date with official "
    "public statements from both governments qualifies as strong evidence.\n"
    "16. REELECTION MARKETS ('Will X win re-election?'): Treat like general election "
    "markets. Incumbents have a modest structural advantage (~52%), but current polling, "
    "approval ratings, and economic conditions dominate near the election date. Avoid "
    "HIGH confidence unless you find a dated, primary-source polling average with a "
    "clear and sustained lead (>5pp in likely-voter models) within the past 30 days.\n"
    "17. CORPORATE LEADERSHIP MARKETS ('Will X become CEO/CFO/Chair of Y?'): Base rate "
    "is ~35% for any specific appointment within a given window. 'Board is considering', "
    "'rumored front-runner', 'headhunters hired', 'activist pressure mounting', or "
    "'name being floated' are standard pre-appointment steps that frequently do not "
    "materialize. Only a confirmed board announcement or SEC filing (8-K) qualifies as "
    "strong evidence. Media speculation and activist letters do NOT justify >50%.\n"
    "18. UN SECURITY COUNCIL MARKETS ('Will the UNSC pass a resolution on X?'): Base "
    "rate is ~15% due to Chinese and Russian veto risk on most contested topics. "
    "'Widespread Western support', 'draft circulating', or 'strongly worded statement' "
    "are NOT evidence of passage — Russia and China have vetoed dozens of such drafts. "
    "Only a clearly non-contested procedural vote or documented unanimous agreement "
    "justifies an estimate above 30%.\n"
    "19. LEGAL/CRIMINAL PROCEEDINGS MARKETS: Apply category-specific base rates. "
    "Pardon/clemency ('Will X be pardoned?'): ~35% — highly dependent on political climate; "
    "'under consideration' or 'allies lobbying' is NOT a pardon. "
    "Plea deal ('Will X plead guilty?'): ~45% for high-profile contested cases — federal "
    "prosecutors have strong leverage, but prominent defendants often fight charges. "
    "Acquittal ('Will X be acquitted?'): ~35% for prediction-market trials (contested, "
    "high-profile) — conviction rates are high historically but political/celebrity cases "
    "are more mixed. For each, only direct reporting of an imminent agreement or jury "
    "deliberation outcome qualifies as HIGH confidence evidence.\n"
    "20. GOVERNMENT FUNDING / DEBT CEILING MARKETS: Two distinct market types with "
    "opposite base rates. "
    "Shutdown averted ('Will Congress avoid a shutdown by X?'): ~85% — Congress almost "
    "always passes a CR or omnibus at the last minute; treat pessimistic market prices "
    "below 60% as likely underpriced unless specific breakdown evidence is verified. "
    "Shutdown begins ('Will there be a government shutdown in X?'): ~15% — the mirror "
    "of the above. "
    "Debt ceiling raised/suspended ('Will Congress raise the debt ceiling by X?'): ~70% "
    "— default has never occurred; Congress always resolves it, though timing is uncertain. "
    "Generic debt ceiling question (no resolution language): ~65%. "
    "CRITICAL: Distinguish averted/raised (HIGH base rate) from starts/default (LOW base rate).\n"
    "21. GEOPOLITICAL / MILITARY ESCALATION MARKETS: News-cycle intensity is NOT evidence "
    "of probability — base rates for dramatic geopolitical events are very low regardless "
    "of media coverage. Apply these base rates strictly: "
    "Military invasion of a sovereign nation: ~15% per 6-month window; "
    "NATO Article 5 invocation: ~5% (never successfully invoked in modern combat); "
    "US military strike against a named country: ~15%; "
    "Coup or regime change: ~10%. "
    "Escalating rhetoric, troop mobilization headlines, or 'senior officials say X is possible' "
    "are already priced into current market levels — they are NOT independent evidence to raise "
    "your estimate above the base rate. Require a verified, dated incident (confirmed military "
    "action, official government declaration) to deviate meaningfully from base rate. "
    "Default to PASS on geopolitical escalation markets unless edge > 15pp.\n"
    "22. NATURAL DISASTER / WEATHER SEVERITY MARKETS ('Will wildfire burn X million acres?', "
    "'Will hurricane cause $X billion in damage?'): Specific severity thresholds in any given "
    "window are near 50/50 once a hazard has been identified. Base rates: "
    "Wildfire acreage threshold: ~35%; Major hurricane making landfall: ~45% for active seasons; "
    "Earthquake of specific magnitude in a named region: ~30%. "
    "CRITICAL: Weather forecasts, fire weather warnings, and active-season outlooks are "
    "already priced in by the crowd. Do not raise your estimate above the base rate solely "
    "because forecasters say 'conditions are favorable' — that information is public and "
    "already in the market price. Only a confirmed developing event (named storm within 3 days, "
    "active fire already within 50% of the threshold) justifies deviation. Default to PASS.\n"
    "23. AI CAPABILITY MILESTONE MARKETS ('Will AI pass the MCAT?', 'Will AI outperform "
    "doctors on X?', 'Will AI achieve Y score on Z benchmark?'): Distinct from model RELEASE "
    "markets (Rule 8) — these ask whether AI will demonstrate a specific capability, not "
    "whether it will be released. Base rates for any 6-month window: AI passing a professional "
    "exam (bar, MCAT, CPA) ~40%; AI achieving AGI by a specific date <5%; AI passing a "
    "specific coding competition ~50%. CRITICAL: You have a training-data bias toward AI "
    "optimism — successful benchmarks get press coverage while failures are buried. Correct "
    "for this by applying base rates conservatively. 'Achieves near-human performance', "
    "'shows remarkable capabilities', or 'beats previous state of the art' are NOT evidence "
    "of meeting the specific threshold in the market question. Default to PASS unless edge > 12pp.\n"
    "24. BANK FAILURE / FINANCIAL SYSTEM RISK MARKETS ('Will X bank fail?', 'Will there be "
    "a banking crisis?'): Base rates for any given window: Specific named bank failure ~15%; "
    "Systemic banking crisis requiring Fed emergency action ~10%; Regional bank stress "
    "resulting in failure ~20%. CRITICAL: 'Share price declining', 'analyst downgrades', "
    "'liquidity concerns reported', 'stress test scenario', and 'short sellers increasing "
    "positions' are routine banking news that precedes actual failure in only a small "
    "fraction of cases — they are NOT independent evidence above base rate. Only FDIC "
    "intervention, confirmed resolution proceedings, or a verified bank run with documented "
    "deposit outflows qualifies as strong evidence. Default to PASS on bank failure markets "
    "unless you find official regulatory action already taken.\n"
    "25. EMERGING TECHNOLOGY READINESS MARKETS ('Will fully autonomous vehicles be "
    "commercially available?', 'Will quantum computing break encryption by Y?', 'Will "
    "humanoid robots be sold commercially by Z?'): Base rates for a given 6-12 month window: "
    "Full self-driving commercial availability (L4/L5, not geofenced) ~25%; Quantum computing "
    "breaking current RSA encryption <5%; Consumer humanoid robot available at scale ~15%; "
    "Commercial nuclear fusion power <5%. CRITICAL: Technology demonstrations, press "
    "releases, and 'limited pilot programs' are NOT evidence of the broad commercial or "
    "capability threshold typically asked. Regulatory approval ≠ commercial deployment. "
    "A controlled-environment demonstration ≠ general availability. Announced technology "
    "timelines in this space slip by 2-5x on average. Default to PASS.\n"
    "26. CLIMATE / ENVIRONMENTAL RECORDS MARKETS ('Will 2026 be the hottest year on "
    "record?', 'Will global average temperature exceed X°C in Y?'): Different from natural "
    "disaster severity (Rule 22) — records are more trend-predictable but near-coin-flip "
    "for any specific year. Base rates: Hottest year on record in any given recent year "
    "~40%; Specific annual global temperature threshold in a given year ~35-50% depending "
    "on current trajectory. CRITICAL: Climate trend extrapolation is already priced in — "
    "'scientists say it will likely be warm' and 'early months are on pace' are already "
    "reflected in market prices. Only a verifiable multi-month YTD anomaly that definitively "
    "confirms the record is already locked in justifies deviation. Default to PASS unless "
    "the current price is more than 20pp from 50%.\n"
    "27. CRYPTOCURRENCY / DIGITAL ASSET MARKETS ('Will Bitcoin reach $X?', 'Will ETH "
    "price exceed $Y by Z?', 'Will crypto market cap reach $W?'): Rule 13 (price-level "
    "markets) applies strictly, with an additional volatility caveat. Base rates: Bitcoin "
    "reaching a price 50% above current level within 3 months ~25%; reaching 2x within "
    "6 months ~20%; any specific crypto price threshold near current price near 50/50. "
    "CRITICAL: Crypto markets are driven by macro sentiment, regulatory news, and "
    "speculative momentum — all already priced in by the highly active trading community. "
    "'Institutional adoption', 'ETF flows', 'halving cycle', and 'on-chain data suggests "
    "X' are widely cited and already reflected in market prices. Default to PASS on all "
    "crypto price-level markets unless the current price is more than 25pp from 50%.\n"
    "28. SHORT-HORIZON EDGE DECAY (markets labeled INTRADAY or WEEKLY — closing within "
    "7 days): For markets closing within 7 days, the current market price is more "
    "informative than long-run heuristic base rates. Recent market participants have "
    "had more opportunity to price in new information, so the crowd is better calibrated "
    "for near-term outcomes. Apply a HIGHER bar for deviating from the current price: "
    "require at least 15pp edge (not the standard 10pp from Rule 11) AND primary-source "
    "evidence dated within the past 72 hours. Heuristic base rates such as 'legislative "
    "bills pass ~35% of the time' or 'military strikes happen ~15% of the time' reflect "
    "long-run frequencies across many years — they are LESS applicable to a market "
    "closing in 3 days because that market is priced on THIS specific situation right now. "
    "Avoid HIGH confidence on INTRADAY/WEEKLY markets unless the event has already "
    "occurred (confirmed in primary sources) or the market is clearly mispriced due to "
    "a lag in the order book. Default to PASS on short-horizon heuristic-flagged markets "
    "unless the evidence is exceptionally clear and recent.\n"
    "29. LV SCORE GRADE — EDGE THRESHOLD SCALING: Each market prompt includes a "
    "SIGNAL QUALITY line showing a pre-computed Leviathan Score grade (A/B/C/D). "
    "This grade reflects the aggregate quality of the heuristic signal stack — whale "
    "activity, persistence, convergence, and spread cost — BEFORE you apply web "
    "research. Use it to scale the minimum edge required to commit to YES or NO:\n"
    "  Grade A (LV ≥70) — multiple independent indicators aligned, spread is low: "
    "accept YES/NO at edge ≥7pp. The heuristic foundation is strong; your research "
    "burden is confirming, not proving.\n"
    "  Grade B (LV 55-69) — solid signal, standard bar: apply the standard Rule 11 "
    "threshold of edge ≥10pp. No adjustment.\n"
    "  Grade C (LV 40-54) — marginal signal stack: raise bar to edge ≥12pp. Require "
    "corroborating evidence before YES/NO; prefer PASS if evidence is ambiguous.\n"
    "  Grade D (LV <40) — weak heuristic foundation, likely high spread or no "
    "persistence: raise bar to edge ≥15pp. The scanner flagged this but the signal "
    "quality is poor — default to PASS unless the fundamental evidence is overwhelming "
    "and primary-source dated. Note: Rule 28 (short-horizon) thresholds are additive "
    "when both apply — take the higher of the two requirements.\n"
    "30. ANCHORING GUARD — WEB EVIDENCE DRIVES THE ESTIMATE, NOT THE BASE RATE: "
    "The scanner flags a market because a heuristic base rate diverges from the current "
    "market price. Your job is to find evidence that either confirms or refutes that "
    "divergence — NOT to anchor your estimate to the base rate. When you search the web "
    "and find weak, ambiguous, or no clear evidence, your estimate must move TOWARD the "
    "market price, not toward the base rate. The market price already incorporates "
    "heuristic base rates, prior news, and trader judgment. To generate edge, you need "
    "something the market does not have — recent, specific, primary-source evidence. "
    "Example: base rate 55%, market at 48%, you search and find only generic background "
    "articles with no recent specifics → your estimate should be ~49-51% (near market), "
    "not 55% (base rate), because you found no incremental information beyond what the "
    "market already knows. Only concrete, recent, specific evidence justifying a material "
    "probability shift (≥7pp for Grade A, ≥10pp default, ≥12pp Grade C, ≥15pp Grade D) "
    "should move you far from the current market price.\n"
    "31. FDA / DRUG REGULATORY APPROVAL MARKETS ('Will [drug] receive FDA approval "
    "by [date]?', 'Will the FDA approve [biologic] NDA?'): These have specific, "
    "data-driven base rates you must apply. Base rates for markets asking about "
    "approval BY a deadline near the PDUFA date: ~85-90% (once NDA/BLA filed, most "
    "drugs get approved around the PDUFA date). Base rates for earlier deadlines "
    "(before PDUFA date): ~60-70% (depends on clinical data strength). Prior Complete "
    "Response Letter (CRL): ~60% on resubmission. First-in-class novel mechanism: "
    "~65-75%. Accelerated/Priority Review: ~88% once at FDA. Biosimilar: ~90%+. "
    "CRITICAL: 'Strong Phase 3 results', 'advisory committee voted favorably', and "
    "'PDUFA date confirmed' are NOT edge over the base rate — these are standard "
    "steps that already apply to the ~85% that get approved. Only an FDA Refuse-to-"
    "File letter, an adcomm unexpected rejection, or a clinical hold is meaningful "
    "downside evidence. A confirmed PDUFA date within the market deadline justifies "
    "a high YES estimate (≥80%) unless there is documented regulatory concern. "
    "Default to PASS if the market deadline is well before the PDUFA date.\n"
    "32. SPORTS MATCH OUTCOME MARKETS ('Will [team] win on [date]?', "
    "'Will [team] cover the spread?', O/U totals, player props): These markets are "
    "among the most efficiently priced on any prediction platform. Sports books "
    "employ full-time quant teams; closing line probabilities are nearly unbeatable. "
    "Base rates: favorite wins roughly at the implied market probability; upsets "
    "happen at the exact frequency the market predicts. CRITICAL: 'Team X has been "
    "playing well', 'Player Y is in form', 'Head-to-head record favors Z' are "
    "already reflected by professional bettors who track every statistic. You have "
    "no information advantage over Polymarket or Kalshi sports markets. Default to "
    "PASS on all sports match outcome, spread, O/U, and player prop markets unless "
    "the gap vs. another platform is ≥15pp AND you can identify a specific pricing "
    "error (e.g., confirmed injury not yet reflected, lineup change).\n"
    "33. SOCIAL MEDIA POST / TWEET MARKETS ('Will [person] tweet about X by Y?', "
    "'Will [account] post about X?', 'Will [person] mention X on Twitter/X/Instagram?'): "
    "If the person is an active social media user who regularly discusses the topic "
    "category, the base rate is very high: ~75-85% for any specific 1-month window. "
    "Base rates by framing: 'Will [active user] tweet/post about [topic they regularly cover]' "
    "~80%; 'Will [active user] tweet/post about [topic outside their usual domain]' ~40%; "
    "'Will [dormant/banned account] post' ~5%. CRITICAL: Active social media users tweet "
    "constantly — 'hasn't tweeted about this recently' is NOT evidence they won't. Only "
    "a confirmed account suspension, ban, or prolonged verified absence from the platform "
    "substantially lowers the rate below 70%. For Trump on Truth Social or X: base rate "
    "for any political/news topic is ~90%. For Elon Musk tweeting about any tech/policy "
    "topic: ~85%. Default to YES (high estimate near 80%) unless clear account-level "
    "evidence of inactivity or ban.\n"
    "34. CORPORATE PARTNERSHIP / DEAL ANNOUNCEMENT MARKETS ('Will [Company A] announce "
    "a partnership with [Company B]?', 'Will [Company] sign a deal with [Company]?', "
    "'Will [Company] enter a licensing agreement with [Company]?'): Base rate ~35% for "
    "any specific partnership announcement within a 3-6 month window. CRITICAL: Apply "
    "the same skepticism as Rules 3 and 5. 'In advanced talks', 'exploring a partnership', "
    "'strategic discussions ongoing', 'companies are in negotiations', and 'due diligence "
    "underway' are STANDARD pre-announcement steps that frequently do not materialize into "
    "signed agreements. Only an officially signed, publicly announced binding agreement "
    "(press release from both companies, regulatory filing, or definitive contract disclosed) "
    "qualifies as HIGH confidence evidence. 'People familiar with the matter say talks are "
    "progressing' is ONE source, not corroboration — Rule 2 applies. Default to PASS "
    "unless you find an actual signed agreement or imminent, confirmed joint press release.\n"
    "35. FEDERAL RESERVE / FOMC RATE DECISION MARKETS ('Will the Fed cut rates at the "
    "[Month] meeting?', 'Will the Fed raise rates at [FOMC meeting]?', 'Will the FOMC "
    "hold rates in [Month]?'): The CME FedWatch tool gives the market-implied probability "
    "for any specific FOMC meeting outcome, updated in real time. This probability is "
    "EXTREMELY well-calibrated — it integrates all public information including CPI, PCE, "
    "jobs reports, GDP, and Fed communications. CRITICAL: Any information you find via web "
    "search ('Fed signals dovish pivot', 'inflation data suggests cut', 'Powell hints at "
    "pause') is ALREADY priced into the CME FedWatch probability. Do NOT adjust your "
    "estimate away from the current market price based on news rhetoric — you have no "
    "information advantage over futures traders. The correct approach: search for the "
    "current CME FedWatch implied probability for the specific meeting, compare it to the "
    "Kalshi market price, and use the gap (if any) as your signal. If CME FedWatch says "
    "72% for a cut and Kalshi says 68%, a 4pp gap is noise — PASS. Only a ≥10pp divergence "
    "between CME FedWatch and Kalshi market price justifies YES/NO. Default to PASS on "
    "all specific-meeting FOMC rate decision markets unless CME FedWatch diverges ≥10pp.\n"
    "Rule 35 Addendum — MULTI-MEETING RATE PATH MARKETS ('Will the Fed cut rates by "
    "[Month]?', 'How many cuts in 2026?'): These are priced by the futures curve, not "
    "single-meeting probabilities. Same principle applies: the futures strip already "
    "incorporates all public information. Do NOT try to forecast the rate path independently "
    "from your own macro analysis — futures traders with sophisticated models are already "
    "doing this. Only structural breaks (confirmed financial crisis, surprise emergency "
    "action) would create enough divergence to trade. Default to PASS.\n"
    "36. STOCK INDEX INCLUSION MARKETS ('Will [Company] be added to the S&P 500?', "
    "'Will [Company] join the Nasdaq 100 by [date]?', 'Will [Company] be included in "
    "the Russell 2000?'): Base rate ~50% for any eligible company within a 6-month "
    "window. The S&P 500 committee meets quarterly for scheduled rebalancing and can "
    "add companies at any meeting. CRITICAL: The key factor is whether the company "
    "has met ALL criteria: (1) market cap ≥ $20B, (2) positive GAAP earnings in the "
    "most recent quarter AND cumulative over 4 quarters, (3) U.S. domicile, (4) "
    "adequate liquidity (float ≥ $10B, daily volume). 'Analysts expect', 'market cap "
    "qualifies', and 'long rumored for inclusion' are NOT evidence — the committee "
    "does not pre-announce additions. Only an official S&P Dow Jones Indices press "
    "release announcing the addition is HIGH confidence. Verify the company has met "
    "all four criteria via current filings; market cap alone is insufficient. Adjust "
    "estimate upward from 50% if: recently profitable (just cleared earnings criteria), "
    "index vacancy exists (company was removed), or float is extremely high. Adjust "
    "downward if: company recently failed an earnings criterion, or the market window "
    "falls between scheduled rebalancing dates with no special announcement expected.\n"
    "37. BLOCKCHAIN / CRYPTO NETWORK UPGRADE MARKETS ('Will Ethereum complete the [upgrade] "
    "by [date]?', 'Will Bitcoin activate [BIP] by [block]?', 'Will the [chain] hard fork "
    "succeed by [date]?'): Scheduled protocol upgrades that have completed testnet deployment "
    "and received governance approval almost always activate — base rate ~85-90%. Uncontested "
    "upgrades (≥95% client adoption, no known dissent) should be treated as near-certain. "
    "Contentious forks (significant miner/validator opposition, competing chain risk): "
    "base rate ~50% for the majority chain outcome. Upgrades not yet through testnet: "
    "base rate ~65% (technical issues can delay but rarely kill scheduled upgrades). "
    "CRITICAL: Unlike product launches (Rule 3/8), protocol upgrades are NOT about a "
    "company's schedule slipping — they are about network consensus. A confirmed EIP/BIP "
    "number, completed shadow fork, and set activation block/timestamp is very strong "
    "evidence. 'No major objections from core developers' and 'testnet successful' are "
    "meaningful confirmations, not mere announcements. Only confirmed block reorganizations, "
    "emergency governance veto, or critical security vulnerability should substantially "
    "lower the estimate below 75% for a testnet-cleared upgrade.\n"
    "38. SECONDARY EQUITY OFFERING / CREDIT RATING CHANGE MARKETS:\n"
    "  Secondary offering ('Will [Company] complete a follow-on offering by [date]?'): "
    "Base rate ~35% for rumors/exploration. If a company has filed an S-3 shelf "
    "registration and has an active ATM program, base rate rises to ~75-80% for "
    "completing a raise within any 6-month window. If an underwritten offering is priced "
    "(term sheet out, bookrunners named, price range set), treat as near-certain (≥90%). "
    "CRITICAL: 'Exploring equity raise', 'in talks with banks', 'considering a secondary' "
    "are standard corporate treasury discussions — not imminent offerings. Only a filed "
    "prospectus or confirmed priced deal qualifies as HIGH confidence. Rule 3 applies.\n"
    "  Credit rating change ('Will [Country/Company] be downgraded by [Agency]?'): "
    "Base rate ~40% once on negative watch or outlook negative. For sovereign debt: "
    "watch S&P/Moody's/Fitch 'outlook negative' designation — this leads to actual "
    "downgrade within 24 months ~60% of the time. A company already on CreditWatch "
    "Negative (S&P) or review for downgrade (Moody's) has ~70% probability of action "
    "within 90 days. 'Concerns raised', 'leverage is elevated', or 'analysts warn of "
    "downgrade risk' without formal outlook change is background noise — treat as 40% "
    "base rate. Only the formal outlook/watch designation raises the estimate meaningfully."
)

RESPONSE_SCHEMA = """
Return a JSON array where each element has exactly these fields:
{
  "ticker": "string — Kalshi ticker",
  "market_price": 0.00,
  "our_estimate": 0.00,
  "edge": 0.00,
  "direction": "YES | NO | PASS",
  "confidence": "HIGH | MED | LOW",
  "reasoning": "2-3 sentences max",
  "sources_checked": ["headline or url"]
}

direction = "YES" if our_estimate > market_price and edge is worth acting on
direction = "NO" if our_estimate < market_price and edge is worth acting on
direction = "PASS" if edge is not meaningful or evidence is unclear
"""


def build_system_prompt(calibration: dict | None = None,
                        flag_cal: list | None = None) -> str:
    """
    Returns the system prompt, optionally with historical calibration feedback.

    calibration: dict from logger.get_stats_by_confidence() — keys HIGH/MED/LOW.
    flag_cal: list from logger.get_stats_by_flag_path() — flag-path win rates.
    When resolved data is present, appends a CALIBRATION FEEDBACK section so
    Claude can self-correct. No effect when both params are None/empty.
    """
    if not calibration and not flag_cal:
        return SYSTEM_PROMPT

    lines = ["\n\nCALIBRATION FEEDBACK (your prior calls on this system — use to recalibrate):"]
    has_data = False

    if calibration:
        for lvl in ("HIGH", "MED", "LOW"):
            d = calibration.get(lvl, {})
            total = d.get("total", 0)
            wr    = d.get("win_rate")
            wins  = d.get("wins", 0)
            if total > 0 and wr is not None:
                has_data = True
                lines.append(f"  {lvl} confidence: {wins}/{total} correct ({wr:.0f}% win rate)")

        if has_data:
            lines.append(
                "  Guidance: HIGH confidence should win ≥65% — if below, downgrade borderline "
                "HIGH to MED. MED should win ≥55% — if below, be more conservative on base rates. "
                "LOW below 50% means you are adding noise — prefer PASS instead."
            )

    if flag_cal:
        fp_rows = [r for r in flag_cal if r.get("total", 0) >= 2 and r.get("win_rate") is not None]
        if fp_rows:
            has_data = True
            lines.append("  By signal path (paths with ≥2 resolved signals):")
            for r in fp_rows:
                wr = r["win_rate"]
                total = r["total"]
                path  = r.get("flag_path", "?")
                note  = ""
                if wr >= 65:
                    note = " ← reliable"
                elif wr < 45:
                    note = " ← poor — be more skeptical"
                lines.append(f"    {path}: {wr:.0f}% win rate ({total} resolved){note}")

    if not has_data:
        return SYSTEM_PROMPT

    return SYSTEM_PROMPT + "\n".join(lines)


def _find_claude() -> str:
    """Locate the claude CLI binary."""
    cmd = shutil.which("claude")
    if cmd:
        return cmd
    # Common Windows install paths
    import os
    candidates = [
        r"C:\Users\Administrator\AppData\Local\AnthropicClaude\claude.exe",
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise RuntimeError(
        "claude CLI not found in PATH. "
        "Run Leviathan from a Claude Code terminal, or ensure `claude` is in PATH."
    )


def build_prompt(markets: list[dict]) -> str:
    lines = [
        "Score the following Kalshi prediction markets. For each, search for recent "
        "relevant information and estimate the true probability of YES occurring.\n",
        RESPONSE_SCHEMA,
        "\n--- MARKETS ---\n",
    ]

    from datetime import datetime, timezone as _tz
    _now = datetime.now(_tz.utc)

    for i, m in enumerate(markets, 1):
        mid_price = m.get("mid_price")
        whale     = m.get("whale_data")
        horizon   = m.get("time_horizon", "MONTHLY")
        horizon_note = {
            "INTRADAY":  "closes today — weight breaking news and current momentum only",
            "WEEKLY":    "closes within 7 days — near-term catalysts most relevant",
            "MONTHLY":   "closes within 30 days — balance recent news with base rates",
            "QUARTERLY": "closes within 90 days — fundamentals and structural factors carry more weight",
            "LONG":      "closes 90+ days out — base rates and long-run trends dominate",
        }.get(horizon, "")

        close_str = m.get("close_time") or m.get("expiration_time", "")
        days_left = None
        if close_str:
            try:
                close_dt  = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                days_left = max(0, (close_dt - _now).days)
            except (ValueError, AttributeError):
                pass
        days_note = f" ({days_left}d remaining)" if days_left is not None else ""

        lines.append(f"{i}. [{m.get('ticker', '')}] {(m.get('title', ''))[:120]}")
        lines.append(f"   Horizon: {horizon} ({horizon_note})")
        lines.append(f"   Current market price (YES): {f'{mid_price * 100:.1f}%' if mid_price is not None else 'unknown'}")
        lines.append(f"   Closes: {close_str}{days_note}")

        # Tell Claude WHY this market was flagged
        fp = m.get("flag_path")
        if fp == "HEURISTIC":
            br     = m.get("base_rate")
            hd     = m.get("heuristic_direction")
            br_str = f"base rate {br*100:.0f}%" if br is not None else "base rate unknown"
            lean   = f" — leans {hd}" if hd and hd != "NEUTRAL" else ""
            lines.append(f"   FLAG REASON: HEURISTIC — {br_str} vs market price{lean}")
        elif fp == "DRIFT":
            lines.append(f"   FLAG REASON: DRIFT — market price has moved significantly from last traded price")
        elif fp == "WATCHLIST":
            lines.append(f"   FLAG REASON: WATCHLIST — top Polymarket traders have open positions on this market")
        elif fp == "EDGE":
            br     = m.get("base_rate")
            hd     = m.get("heuristic_direction")
            br_str = f"base rate {br*100:.0f}%" if br is not None else "base rate unknown"
            lean   = f" — leans {hd}" if hd and hd != "NEUTRAL" else ""
            lines.append(f"   FLAG REASON: EDGE — heuristic {br_str} vs market price{lean}")
        elif fp == "CROSS_MARKET":
            poly = m.get("poly") or {}
            gap_pct = abs((poly.get("price_gap") or 0) * 100)
            direction = "higher" if (poly.get("price_gap") or 0) > 0 else "lower"
            lines.append(
                f"   FLAG REASON: CROSS_MARKET — no heuristic or drift signal, but the equivalent "
                f"Polymarket question is priced {gap_pct:.0f}% {direction} than Kalshi. "
                f"Determine whether Kalshi or Polymarket is better calibrated for this event."
            )

        # Pre-computed Leviathan Score — composite signal quality (0-100, A-D band)
        _lv = compute_leviathan_score(m)
        _lv_band = "A" if _lv >= 70 else "B" if _lv >= 55 else "C" if _lv >= 40 else "D"
        _lv_hint = {
            "A": "strong pre-signal conviction — proceed with careful web research",
            "B": "solid pre-signal — verify the key assumption with web search",
            "C": "marginal — confirm edge is real before committing to YES/NO",
            "D": "weak signal stack — high bar for YES/NO; prefer PASS unless evidence is clear",
        }[_lv_band]
        lines.append(f"   SIGNAL QUALITY: LV {_lv}/100 (Grade {_lv_band}) — {_lv_hint}")

        # Short-horizon warning — reinforce Rule 28 in context.
        if m.get("short_horizon"):
            lines.append(
                f"   [!] SHORT HORIZON: This market closes within 7 days. Heuristic base rates "
                f"are long-run averages — they do NOT carry predictive signal over a 1-7 day "
                f"window. Require 15pp edge AND evidence dated within 72 hours. "
                f"Default to PASS unless you find a very recent, specific, primary-source catalyst."
            )

        # Cross-market conflict warnings — explicitly flag when key sources disagree.
        _hd  = m.get("heuristic_direction")
        _pg  = float((m.get("poly") or {}).get("price_gap") or 0)
        _cg  = float((m.get("ext_consensus") or {}).get("consensus_gap") or 0)
        _cdir = (m.get("ext_consensus") or {}).get("consensus_dir")
        if _hd in ("YES", "NO") and abs(_pg) >= 0.05:
            _poly_dir = "YES" if _pg > 0 else "NO"
            if _hd != _poly_dir:
                lines.append(
                    f"   [!] HEURISTIC vs POLYMARKET CONFLICT: Base rate says {_hd} is "
                    f"underpriced; Polymarket says {_poly_dir}. Polymarket reflects real money "
                    f"and current information — weight it heavily. Require primary-source "
                    f"evidence to side against Polymarket."
                )
        elif _hd in ("YES", "NO") and abs(_cg) >= 0.05 and _cdir and _hd != _cdir:
            n_opp = ((m.get("ext_consensus") or {}).get("sources_higher", 0)
                     if _cdir == "YES" else
                     (m.get("ext_consensus") or {}).get("sources_lower", 0))
            if n_opp >= 2:
                lines.append(
                    f"   [!] HEURISTIC vs CONSENSUS CONFLICT: Base rate says {_hd}; "
                    f"{n_opp} external platform(s) say {_cdir}. Treat multi-platform "
                    f"divergence from the heuristic as a caution signal."
                )

        # Signal alignment summary — show how many independent sources agree on direction.
        # Only appears when ≥2 sources have a directional opinion (≥5pp from neutral).
        _s_yes = 0
        _s_no  = 0
        _hd    = m.get("heuristic_direction")
        if _hd == "YES":                                    _s_yes += 1
        elif _hd == "NO":                                   _s_no  += 1
        _poly = m.get("poly") or {}
        _pg = _poly.get("price_gap", 0) or 0
        if abs(_pg) >= 0.05:
            if _pg > 0:                                     _s_yes += 1
            else:                                           _s_no  += 1
        _cons = (m.get("ext_consensus") or {})
        if abs(_cons.get("consensus_gap", 0) or 0) >= 0.05:
            _cdir  = _cons.get("consensus_dir")
            # Weight by platform count (cap 3): 3 platforms agreeing = 3 votes
            _n_plt = min(3, (_cons.get("sources_higher", 0) if _cdir == "YES"
                              else _cons.get("sources_lower", 0)))
            if _cdir == "YES":                              _s_yes += _n_plt
            elif _cdir == "NO":                             _s_no  += _n_plt
        _drift_pct = m.get("price_drift") or 0
        if m.get("drift_flag"):
            if _drift_pct < 0:                              _s_yes += 1
            else:                                           _s_no  += 1
        _wh = whale or {}
        _wdir = _wh.get("whale_direction")
        if _wh.get("whale_detected") and _wdir in ("YES", "NO"):
            if _wdir == "YES":                              _s_yes += 1
            else:                                           _s_no  += 1
        if m.get("ob_flag"):
            if m.get("ob_direction") == "YES":              _s_yes += 1
            elif m.get("ob_direction") == "NO":             _s_no  += 1
        _sm_n = len([s for s in (m.get("smart_money") or []) if s.get("direction") in ("YES","NO")])
        if _sm_n >= 2:
            _sm_yes = sum(1 for s in (m.get("smart_money") or []) if s.get("direction") == "YES")
            if _sm_yes > _sm_n / 2:                        _s_yes += 1
            elif _sm_yes < _sm_n / 2:                      _s_no  += 1
        if m.get("watchlist_signal"):
            _wl_dir = (m.get("watchlist_direction") or "").upper()
            if _wl_dir == "YES":                           _s_yes += 1
            elif _wl_dir == "NO":                          _s_no  += 1

        _total_s = _s_yes + _s_no
        if _total_s >= 2:
            _lean  = "YES" if _s_yes > _s_no else ("NO" if _s_no > _s_yes else "MIXED")
            _align = "ALL" if (_s_yes == 0 or _s_no == 0) else f"{max(_s_yes, _s_no)}/{_total_s}"
            # Append recent-activity flags to summary line when present
            _recent = []
            _vt = float(m.get("volume_fp") or m.get("volume") or 0)
            _v24 = float(m.get("volume_24h_fp") or 0)
            if _vt > 0 and _v24 > 0 and (_v24 / _vt) >= 0.20:
                _recent.append("VOL_SPIKE")
            _pp = float(m.get("previous_price_dollars") or 0)
            _lp = float(m.get("last_price_dollars") or 0)
            if _pp > 0 and _lp > 0 and abs((_lp - _pp) / _pp) >= 0.20:
                _recent.append("PRICE_JUMP")
            _extra = f"  [{', '.join(_recent)}]" if _recent else ""
            lines.append(f"   SIGNAL SUMMARY: {_total_s} source(s) → {_align} lean {_lean}{_extra}")

        if whale and whale.get("whale_detected"):
            lines.append(
                f"   WHALE ALERT: Large trades detected buying {whale.get('whale_direction', 'unknown')}. "
                f"Max trade size: {whale.get('max_trade_size', 0):.0f} (avg: {whale.get('avg_trade_size', 0):.1f})"
            )

        if m.get("whale_reversal"):
            lines.append(
                f"   REVERSAL SIGNAL: Whale buying {(whale or {}).get('whale_direction', 'unknown')} while "
                f"price is trending the opposite direction — possible informed contrarian positioning."
            )

        if m.get("drift_flag"):
            drift_pct = (m.get("price_drift") or 0) * 100
            lines.append(
                f"   DRIFT SIGNAL: Order-book mid is {abs(drift_pct):.1f}% "
                f"{'above' if drift_pct > 0 else 'below'} last traded price — mean reversion candidate."
            )
            br = m.get("base_rate")
            if br is not None and mid_price is not None:
                drift_says_up   = drift_pct < 0   # mid < last → mean revert up → buy YES
                heuristic_says_up = br > mid_price  # base_rate > mid → YES is underpriced → buy YES
                if drift_says_up != heuristic_says_up:
                    drift_call     = "YES" if drift_says_up     else "NO"
                    heuristic_call = "YES" if heuristic_says_up else "NO"
                    lines.append(
                        f"   SIGNAL CONFLICT: DRIFT suggests {drift_call} (mean revert) but "
                        f"BASE RATE ({br*100:.0f}%) suggests {heuristic_call}. "
                        f"Weight the base rate over drift for fundamental mispricing; "
                        f"use drift only as a secondary timing cue."
                    )

        if m.get("spread_wide"):
            lines.append(
                f"   SPREAD SIGNAL: Bid/ask spread is {(m.get('spread_pct') or 0) * 100:.1f}% of mid — "
                f"market maker uncertainty, higher probability of mispricing."
            )

        ext  = m.get("ext_markets") or []
        cons = m.get("ext_consensus") or {}
        if ext:
            ext_lines = []
            for e in ext[:4]:
                gap = e.get("price_gap", 0) * 100
                ext_lines.append(
                    f"{e['source']}: {e['probability']*100:.1f}% ({gap:+.1f}% vs Kalshi, match {e['match_score']:.2f})"
                )
            if cons.get("consensus_dir"):
                avg_pct = (cons.get("avg_ext_price") or 0) * 100
                cgap    = (cons.get("consensus_gap")  or 0) * 100
                ext_lines.append(
                    f"Consensus ({cons['sources_higher']} higher, {cons['sources_lower']} lower): "
                    f"avg {avg_pct:.1f}% ({cgap:+.1f}% vs Kalshi) → lean {cons['consensus_dir']}"
                )
            lines.append("   CROSS-MARKET:")
            for el in ext_lines:
                lines.append(f"   · {el}")

        smart_money = m.get("smart_money") or []
        if smart_money:
            yes_t = [s for s in smart_money if s.get("direction") == "YES"]
            no_t  = [s for s in smart_money if s.get("direction") == "NO"]
            sm_parts = []
            if yes_t:
                avg_pnl = sum(s["avg_pct_pnl"] for s in yes_t) / len(yes_t)
                sm_parts.append(f"{len(yes_t)} winning wallet(s) buying YES (avg portfolio +{avg_pnl:.1f}%)")
            if no_t:
                avg_pnl = sum(s["avg_pct_pnl"] for s in no_t) / len(no_t)
                sm_parts.append(f"{len(no_t)} winning wallet(s) buying NO (avg portfolio +{avg_pnl:.1f}%)")
            lines.append(f"   SMART MONEY: {'; '.join(sm_parts)}")

        poly = m.get("poly")
        if poly and poly.get("price_gap") is not None:
            gap       = poly["price_gap"] * 100
            direction = "higher" if gap > 0 else "lower"
            lines.append(
                f"   POLYMARKET: Equivalent market priced at {poly['poly_price'] * 100:.1f}% "
                f"({abs(gap):.1f}% {direction} than Kalshi). "
                f'Match: "{poly["poly_question"][:80]}" (score {poly["match_score"]:.2f})'
            )

        if m.get("ob_flag"):
            imb    = (m.get("ob_imbalance") or 0) * 100
            ob_dir = m.get("ob_direction", "?")
            lines.append(
                f"   ORDER BOOK: {imb:.0f}% of depth is on the {ob_dir} side — "
                f"strong {'buying' if ob_dir == 'YES' else 'selling'} pressure."
            )

        if m.get("watchlist_signal"):
            wl_dir    = m.get("watchlist_direction") or "UNKNOWN"
            wl_val    = m.get("watchlist_position_val")
            wl_ntrade = m.get("watchlist_trader_count", 0)
            val_str   = f" (${wl_val:,.0f} combined)" if wl_val else ""
            dir_str   = f" — pointing {wl_dir}" if wl_dir not in ("UNKNOWN", None) else ""
            trade_str = f"{wl_ntrade} trader(s)" if wl_ntrade else "top Polymarket traders"
            stale_str = " [NOTE: scan data >24h old — positions may have changed]" if m.get("watchlist_stale") else ""
            lines.append(
                f"   WATCHLIST SIGNAL: {trade_str}{val_str} on Polymarket hold significant "
                f"open positions on a related market{dir_str}. These are top-20 traders by "
                f"monthly PnL — weight this signal; they have demonstrated edge over thousands of trades.{stale_str}"
            )

        if m.get("price_trend"):
            lines.append(f"   Price trend: {m['price_trend']}")

        if m.get("base_rate") is not None:
            lines.append(f"   Base rate estimate: {m['base_rate'] * 100:.1f}%")
            _re  = m.get("raw_edge")
            _ne  = m.get("net_edge")
            _sp  = m.get("spread_pct")
            if _re is not None:
                _edge_str = f"   Edge: raw {_re*100:.1f}pp"
                if _ne is not None:
                    _edge_str += f"  |  net-of-spread {_ne*100:.1f}pp"
                    if _ne <= 0:
                        _edge_str += "  [SPREAD CONSUMES EDGE — entry at ask wipes theoretical gain]"
                    elif _ne < 0.05:
                        _edge_str += "  [thin net edge — spread-sensitive]"
                lines.append(_edge_str)

        # PASS history — let Claude know if it has repeatedly declined this market
        pc = m.get("pass_count", 0)
        if pc >= 2:
            lines.append(
                f"   [NOTE: PASS HISTORY] This market has been scored {pc} time(s) in the past "
                f"14 days and returned PASS each time. This may indicate a systematic scanner "
                f"false-positive, OR that conditions have now changed. Re-examine carefully — "
                f"if nothing has meaningfully changed since the last PASS, prefer PASS again."
            )

        # Signal persistence — longitudinal view from DB history
        pa = m.get("prior_appearances", 0)
        if pa > 0:
            prev_yes = m.get("prior_yes", 0)
            prev_no  = m.get("prior_no", 0)
            consistent = m.get("direction_consistent")
            c_tag = " [CONSISTENT]" if consistent else " [MIXED — review carefully]"
            lines.append(f"   Signal history:  flagged on {pa} distinct day(s) in past 14d")
            if prev_yes or prev_no:
                lines.append(f"     Prior directions: YES x{prev_yes} / NO x{prev_no}{c_tag}")
            first_p = m.get("first_flagged_price")
            cur_p   = float(m.get("market_price") or m.get("mid_price") or 0)
            if first_p is not None and cur_p > 0:
                delta_pp = (cur_p - float(first_p)) * 100
                if abs(delta_pp) >= 1.0:
                    hd = m.get("heuristic_direction") or (
                        "YES" if prev_yes >= prev_no else "NO"
                    )
                    if hd == "YES":
                        price_note = (
                            "price fell — mispricing deepened, stronger entry"
                            if delta_pp < 0 else
                            "price rose — market converging to estimate, tightening edge"
                        )
                    else:
                        price_note = (
                            "price rose — mispricing deepened, stronger entry"
                            if delta_pp > 0 else
                            "price fell — market converging to estimate, tightening edge"
                        )
                    lines.append(
                        f"     Price since first flag: {float(first_p)*100:.1f}% → "
                        f"{cur_p*100:.1f}% ({delta_pp:+.1f}pp — {price_note})"
                    )

        vol_total = float(m.get("volume_fp") or m.get("volume") or 0)
        vol_24h   = float(m.get("volume_24h_fp") or 0)
        if vol_total > 0 and vol_24h > 0:
            vol_pct = vol_24h / vol_total * 100
            if vol_pct >= 20:
                lines.append(
                    f"   VOLUME SPIKE: {vol_24h:.0f} contracts traded in past 24h "
                    f"({vol_pct:.0f}% of total {vol_total:.0f}) — recent market activity is elevated."
                )

        prev_p = float(m.get("previous_price_dollars") or 0)
        last_p = float(m.get("last_price_dollars") or 0)
        if prev_p > 0 and last_p > 0:
            jump_pct = (last_p - prev_p) / prev_p * 100
            if abs(jump_pct) >= 20:
                dir_word = "UP" if jump_pct > 0 else "DOWN"
                lines.append(
                    f"   PRICE JUMP: Last traded price moved {dir_word} {abs(jump_pct):.0f}% "
                    f"vs previous snapshot ({prev_p*100:.1f}% → {last_p*100:.1f}%)."
                )

        # Liquidity context — helps Claude calibrate confidence in the market price
        oi  = float(m.get("open_interest_fp") or m.get("open_interest") or 0)
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        if vol > 0:
            oi_note = f", OI {oi:.0f}" if oi > 0 else ""
            lines.append(f"   Liquidity: {vol:.0f} total volume{oi_note} contracts")

        lines.append("")

    return "\n".join(lines)


def score_markets(flagged_markets: list[dict], config: dict,
                  calibration: dict | None = None,
                  flag_cal: list | None = None) -> tuple[list[dict], dict]:
    """
    Scores a batch of flagged markets using the local claude CLI.
    Returns (scored_markets, token_info).
    token_info is empty — no API billing when using Pro subscription via CLI.

    calibration: from logger.get_stats_by_confidence() — injects confidence win rates.
    flag_cal: from logger.get_stats_by_flag_path() — injects flag-path win rates.
    Both are optional; when present with resolved data, anchor Claude's calibration.
    """
    if not flagged_markets:
        return [], {}

    # Pre-Claude LV gate: drop markets whose pre-scoring LV (computed with LOW
    # confidence = 0 bonus) is so weak that even HIGH confidence (+20) couldn't
    # lift them to Grade C (LV ≥ 40). Default threshold: pre_lv < 20.
    # These consume a slot without realistic path to an actionable signal.
    min_pre_lv = int(config.get("scoring", {}).get("min_pre_claude_lv", 20))
    if min_pre_lv > 0:
        flagged_markets = [
            m for m in flagged_markets
            if compute_leviathan_score(m) >= min_pre_lv
        ]
        if not flagged_markets:
            return [], {}

    max_markets = config.get("scoring", {}).get("max_markets_per_run", 20)
    batch       = flagged_markets[:max_markets]
    user_prompt = build_prompt(batch)
    sys_prompt  = build_system_prompt(calibration, flag_cal=flag_cal)
    claude_cmd  = _find_claude()

    # Exclude ANTHROPIC_API_KEY so the CLI uses Pro OAuth instead of the (empty) API key
    import os as _os
    import time as _time
    clean_env = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}

    max_retries = 2
    result = None
    for attempt in range(max_retries + 1):
        result = subprocess.run(
            [
                claude_cmd,
                "--print",
                "--system-prompt", sys_prompt,
                "--allowedTools", "WebSearch",
                "--output-format", "text",
            ],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            env=clean_env,
        )
        if result.returncode == 0:
            break
        if attempt < max_retries:
            _time.sleep(5)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"scorer.py: claude CLI returned exit {result.returncode} "
            f"after {max_retries + 1} attempt(s): {err[:300]}"
        )

    all_text = result.stdout.strip()
    if not all_text:
        raise RuntimeError("scorer.py: claude CLI returned empty output")

    # Extract JSON array from response
    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", all_text, re.DOTALL)
    if fence_match:
        raw_json = fence_match.group(1).strip()
    else:
        start, end = all_text.find("["), all_text.rfind("]")
        raw_json = all_text[start:end + 1] if start != -1 and end > start else all_text

    try:
        scored = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"scorer.py: Failed to parse JSON: {e}\nRaw: {raw_json[:500]}")

    # No API billing — return empty token info
    return scored, {}
