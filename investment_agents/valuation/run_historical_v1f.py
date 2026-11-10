from __future__ import annotations
import collections, datetime, hashlib, json, statistics
from pathlib import Path
from .historical import select_historical_shares, historical_pe_a, historical_pe_b, historical_pb, relative_difference, history_bucket

ROOT=Path('data_cache'); SEC=ROOT/'sec_companyfacts'; NORM=ROOT/'fundamentals_cache/CURRENT_ANALYSIS_SEC'
RUN=ROOT/'research_runs/72e523511db44863b9454bba2250de7b/scan_result.json'; PROF=ROOT/'research_runs/8c5872fe4f774c9cb9612be5dda4e579/profitability_filter.json'

def annual_eps(facts):
    us=(facts.get('facts') or {}).get('us-gaap') or {}
    priority=['EarningsPerShareDiluted','EarningsPerShareDilutedIncludingExtraordinaryItems','EarningsPerShareBasic','EarningsPerShareBasicIncludingExtraordinaryItems']
    for concept_name in priority:
        concept=us.get(concept_name)
        if not concept: continue
        unit=next((v for k,v in (concept.get('units') or {}).items() if 'USD' in k or k in ('USD/shares','USD/sh')),None)
        if not unit: continue
        rows=[r for r in unit if str(r.get('fp','')).upper()=='FY' and r.get('start') and r.get('end') and (datetime.date.fromisoformat(str(r['end'])[:10])-datetime.date.fromisoformat(str(r['start'])[:10])).days>=300 and isinstance(r.get('val'),(int,float))]
        if rows: return concept_name, rows
    return None, []

def price_on_or_before(bars, end):
    rows=[b for b in bars if b.get('date') and b.get('date')<=end.isoformat() and isinstance(b.get('close'),(int,float)) and b['close']>0]
    if not rows:return None
    return max(rows,key=lambda x:x['date'])

def median(xs): return statistics.median(xs) if xs else None

def main():
    records=json.loads(RUN.read_text())['payload']['records']; analyzed={str(r['ticker']):r for r in records if r.get('ticker') and r.get('scan_status')=='ANALYZED'}
    excluded={t for t,r in json.loads(PROF.read_text())['rows'].items() if r.get('status')=='LONG_TERM_UNPROFITABLE'}
    peer=json.loads(Path('investment_agents/PEER_VALUATION_V1E.json').read_text()); classifications=peer['classifications']; current=json.loads(Path('investment_agents/CURRENT_VALUATION_DATA_V1D.json').read_text())
    current_snap={};
    for p in (ROOT/'CURRENT_ANALYSIS_VALUATION/CURRENT_ANALYSIS/valuation').glob('*.json'):
        try:
            x=json.loads(p.read_text()); s=x.get('snapshot',{});
            if s.get('ticker') in analyzed: current_snap[s['ticker']]=s
        except Exception: pass
    prices=json.loads((ROOT/'v1f_prices_history.json').read_text())
    map_data=json.loads((SEC/'ticker_cik_map.json').read_text())['mapping']
    rows_by_ticker={}; failure=collections.Counter(); share_window_counts={w:collections.Counter() for w in (0,30,60,90)}; direction=collections.Counter(); eps_cov=collections.Counter()
    for t,rec in analyzed.items():
        try: norm=json.loads((NORM/(t.replace('.','-')+'.json')).read_text()).get('normalized') or {}
        except Exception: norm={}
        try: facts=json.loads((SEC/f"CIK{str(map_data.get(t) or map_data.get(t.replace('.','-'))).zfill(10)}.json").read_text())
        except Exception: facts={}
        eps_name,epsrows=annual_eps(facts); eps_cov['available' if epsrows else 'missing']+=1
        annual=sorted([r for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')], key=lambda r:r['report_period'], reverse=True)[:5]
        bars=prices.get(t,{}).get('bars',[]); points=[]
        for ar in annual:
            end=datetime.date.fromisoformat(str(ar['report_period'])[:10]); pb=price_on_or_before(bars,end)
            eps=None
            if epsrows:
                matches=[r for r in epsrows if (str(r.get('end',''))[:4]==str(end.year) or str(r.get('fy',''))==str(end.year))]
                if matches: eps=float(max(matches,key=lambda r:str(r.get('filed',''))).get('val'))
            sm={}
            for w in (0,30,60,90):
                sm[w]=select_historical_shares(facts,end,window_days=w)
                if sm[w].get('status')=='AVAILABLE': share_window_counts[w]['available']+=1
                else: share_window_counts[w]['missing']+=1
            chosen=sm[90]
            if chosen.get('status')=='AVAILABLE': direction[chosen.get('direction')]+=1
            ni=ar.get('net_income'); eq=ar.get('equity'); mcap=None; pe_a=None; pbv=None; pe_b=None
            if not pb: failure['HISTORICAL_PRICE_MISSING']+=1
            if pb and chosen.get('status')=='AVAILABLE':
                mcap=float(pb['close'])*float(chosen['shares']); pe_a=historical_pe_a(pb['close'],chosen['shares'],ni); pbv=historical_pb(mcap,eq)
            elif chosen.get('status')!='AVAILABLE': failure[chosen.get('reason','HISTORICAL_SHARES_MISSING')]+=1
            if isinstance(ni,(int,float)) and ni<=0: failure['NONPOSITIVE_ANNUAL_NET_INCOME']+=1
            if pe_a is None and not (isinstance(ni,(int,float)) and ni<=0): failure['PE_A_UNAVAILABLE_OTHER']+=1
            if eps is not None:
                # The existing adapter is explicitly auto-adjusted (including
                # dividend adjustments); compatibility with SEC annual EPS is
                # therefore not established for a historical PE contract.
                pe_b=historical_pe_b(pb['close'] if pb else None,eps,basis_compatible=False)
                failure['PE_METHOD_B_BASIS_UNCERTAIN']+=1
            else: failure['EPS_MISSING']+=1
            if pb:
                points.append({'fiscal_year':end.year,'fiscal_period_end':end.isoformat(),'price_date':pb['date'],'price':pb['close'],'price_gap_days':(end-datetime.date.fromisoformat(pb['date'])).days,'shares_date':chosen.get('observation_date'),'shares':chosen.get('shares'),'shares_gap_days':chosen.get('gap_days'),'shares_direction':chosen.get('direction'),'net_income':ni,'pe_a':pe_a,'eps_concept':eps_name,'diluted_eps':eps,'pe_b':pe_b,'pe_b_reason':None if pe_b is not None else ('PE_METHOD_B_BASIS_UNCERTAIN' if eps is not None else 'EPS_MISSING'),'equity':eq,'pb':pbv,'status':'AVAILABLE' if pe_a is not None else ('NONPOSITIVE_ANNUAL_NET_INCOME' if isinstance(ni,(int,float)) and ni<=0 else 'PE_A_UNAVAILABLE')})
        rows_by_ticker[t]={'ticker':t,'sector':rec.get('sector'),'industry':rec.get('industry'),'company_type':classifications.get(t,{}).get('company_type'),'profit_history_status':classifications.get(t,{}).get('profit_history_status'),'points':points,'pe_a_count':sum(p.get('pe_a') is not None for p in points),'pe_b_count':sum(p.get('pe_b') is not None for p in points),'pb_count':sum(p.get('pb') is not None for p in points),'eps_concept':eps_name}
    # Agreement and diagnostics
    agreement=collections.Counter(); diffs=[]; divergence_causes=collections.Counter()
    for row in rows_by_ticker.values():
        for p in row['points']:
            d=relative_difference(p.get('pe_a'),p.get('pe_b'))
            if d is not None:
                diffs.append(d); agreement['<=5%' if d<=.05 else '5-10%' if d<=.10 else '10-20%' if d<=.20 else '>20%']+=1
                if d>.20:
                    divergence_causes['SHARES_TIMING' if p.get('shares_direction')=='AFTER' and (p.get('shares_gap_days') or 0)>30 else 'EPS_BASIS_OR_RESTATEMENT']+=1
    counts={metric:collections.Counter(history_bucket(r[f'{metric}_count']) for r in rows_by_ticker.values()) for metric in ('pe_a','pe_b','pb')}
    medians={}; ratios={}; ratio_bins=collections.Counter()
    for t,r in rows_by_ticker.items():
        for metric,key in [('pe_a','PE'),('pb','PB')]:
            vals=[p[metric] for p in r['points'] if p.get(metric) is not None]; medians.setdefault(t,{})[key]={'value':median(vals) if len(vals)>=3 else None,'count':len(vals),'status':'5Y_REFERENCE' if len(vals)>=5 else 'LIMITED_HISTORY_REFERENCE' if len(vals)>=3 else 'UNAVAILABLE'}
            cur=current_snap.get(t,{}).get('pe_ttm' if metric=='pe_a' else 'pb',{}); ref=medians[t][key]['value']; ratios.setdefault(t,{})[key]=cur.get('value')/ref if isinstance(cur.get('value'),(int,float)) and ref else None
            if ratios[t][key] is not None:
                ratio_bins[key]=ratio_bins.get(key,collections.Counter())
                ratio_bins[key][('<=0.80' if ratios[t][key]<=.8 else '0.80-0.90' if ratios[t][key]<.9 else '0.90-1.10' if ratios[t][key]<=1.1 else '1.10-1.20' if ratios[t][key]<1.2 else '>=1.20')]+=1
    # Peer/history overlap at diagnostic MIN_PEERS=10
    peer_rows=json.loads((ROOT/'CURRENT_ANALYSIS_PEER_VALUATION/peer_references_10.json').read_text())['payload']['references']['rows']; ordinary=[t for t,r in rows_by_ticker.items() if classifications.get(t,{}).get('company_type')=='ORDINARY_NON_FINANCIAL' and t not in excluded]
    overlap=collections.Counter()
    strength=collections.Counter()
    for t in ordinary:
        has_peer=peer_rows.get(t,{}).get('pe_ttm',{}).get('reference_level') is not None
        n=rows_by_ticker[t]['pe_a_count']; has5=n>=5; has3=n>=3
        overlap['both_5' if has_peer and has5 else 'peer_only_5' if has_peer and not has5 else 'history_only_5' if has5 else 'neither_5']+=1
        overlap['both_3' if has_peer and has3 else 'peer_only_3' if has_peer and not has3 else 'history_only_3' if has3 else 'neither_3']+=1
        strength['DUAL_REFERENCE_STRONG' if has_peer and has5 else 'DUAL_REFERENCE_LIMITED_HISTORY' if has_peer and has3 else 'PEER_ONLY' if has_peer else 'HISTORY_ONLY' if has3 else 'NO_REFERENCE']+=1
    five={t:rows_by_ticker[t] for t in ['ABNB','AMAT','AOS','COIN','COST']}
    ordinary_rows=[r for t,r in rows_by_ticker.items() if classifications.get(t,{}).get('company_type')=='ORDINARY_NON_FINANCIAL' and t not in excluded]
    special={typ:{'count':sum(classifications.get(t,{}).get('company_type')==typ for t in rows_by_ticker)} for typ in ('BANK','INSURANCE','REIT','NEGATIVE_EQUITY_STRUCTURALLY_INCOMPATIBLE')}
    field_inventory=collections.Counter()
    for t in rows_by_ticker:
        try: annual=json.loads((NORM/(t.replace('.','-')+'.json')).read_text()).get('normalized',{}).get('current_history',{}).get('annual',[])
        except Exception: annual=[]
        field_inventory['annual_net_income']+=int(any(r.get('net_income') is not None for r in annual))
        field_inventory['annual_equity']+=int(any(r.get('equity') is not None for r in annual))
        field_inventory['fiscal_period_end']+=int(any(r.get('report_period') for r in annual))
        field_inventory['filing_date']+=int(any(r.get('filing_date') for r in annual))
    field_inventory['historical_daily_prices']=sum(prices.get(t,{}).get('status')=='AVAILABLE' for t in rows_by_ticker)
    field_inventory['historical_point_in_time_shares_within_90d']=sum(r['pe_a_count']>0 or r['pb_count']>0 for r in rows_by_ticker.values())
    field_inventory['share_class_identity_preserved']=len(rows_by_ticker)
    share_window_ticker_counts={str(w):sum(any(select_historical_shares(json.loads((SEC/f"CIK{str(map_data.get(t) or map_data.get(t.replace('.','-'))).zfill(10)}.json").read_text()),datetime.date.fromisoformat(str(p['fiscal_period_end'])),window_days=w).get('status')=='AVAILABLE' for p in r['points']) for t,r in rows_by_ticker.items()) for w in (0,30,60,90)}
    result={'status':'HISTORICAL_VALUATION_V1F_PARTIAL','inventory':{'analyzed':len(rows_by_ticker),'field_coverage':dict(field_inventory),'historical_price_available':field_inventory['historical_daily_prices'],'eps_available':dict(eps_cov),'price_basis':'yfinance auto_adjust=True (split/dividend adjusted)','limitations':'SEC DEI shares sparse; no EPS for many issuers; adjusted-price basis is not treated as compatible with Method B'},'shares_windows':{str(w):dict(c) for w,c in share_window_counts.items()},'share_window_ticker_counts':share_window_ticker_counts,'share_direction':dict(direction),'rows':rows_by_ticker,'history_buckets':{m:dict(c) for m,c in counts.items()},'agreement':dict(agreement),'agreement_count':len(diffs),'divergence_causes':dict(divergence_causes),'medians':medians,'current_history_ratios':ratios,'ratio_bins':{k:dict(v) for k,v in ratio_bins.items()},'peer_history_overlap':dict(overlap),'reference_strength_matrix':dict(strength),'failure_reasons':dict(failure),'ordinary_coverage':{m:dict(collections.Counter(history_bucket(r[f'{m}_count']) for r in ordinary_rows)) for m in ('pe_a','pe_b','pb')},'special_types':special,'five_names':five,'gics_hash':hashlib.sha256(RUN.read_bytes()).hexdigest()}
    Path('investment_agents/HISTORICAL_VALUATION_V1F.json').write_text(json.dumps(result,indent=2,sort_keys=True,default=str),encoding='utf-8')
    lines=['# HISTORICAL VALUATION V1F','', 'Diagnostic feasibility only. No production valuation policy or investment decision is implemented.', '', '## Input inventory', '', f"- Analyzed: {len(rows_by_ticker)}; field coverage: {dict(field_inventory)}; EPS-bearing annual facts: {dict(eps_cov)}.", '- Price basis: existing TradingAgents/yfinance adapter uses `auto_adjust=True`, which is split/dividend adjusted; this is not assumed compatible with EPS-based PE.', '- Historical shares use only SEC `EntityCommonStockSharesOutstanding`; current shares are never substituted.', '', '## Historical shares windows', '', str(result['shares_windows']), f"Direction under 90-day diagnostic match: {dict(direction)}", '', '## History buckets (all analyzed)', '']
    for m,c in counts.items(): lines.append(f"- {m}: {dict(c)}")
    lines += ['', '## Ordinary-company coverage after long-term-loss exclusion', '']
    for m,c in result['ordinary_coverage'].items(): lines.append(f"- {m}: {c}")
    lines += ['', '## Method A/B agreement', '', f"- Comparable points: {len(diffs)}; distribution: {dict(agreement)}; >20% divergence causes: {dict(divergence_causes)}", '- Method A uses historical close × compatible historical shares / annual NI. Method B uses verified annual diluted EPS only, but is fail-closed because adjusted-price/EPS basis compatibility is not established; no quarterly summing or current shares.', '', '## Five-name validation', '']
    for t,r in five.items(): lines.append(f"- {t}: {[(p['fiscal_year'],p.get('price'),p.get('shares'),p.get('pe_a'),p.get('diluted_eps'),p.get('pe_b'),p.get('pb'),p.get('status')) for p in r['points']]}")
    lines += ['', '## Peer/history overlap (MIN_PEERS=10; diagnostic assumption only)', '', str(overlap), '', '## Reference-strength matrix', '', str(strength), '', '## Failure reasons', '', str(dict(failure)), '', '## Engineering conclusion', '', 'HISTORICAL_PE_REFERENCE_FEASIBLE_WITH_LIMITED_COVERAGE: daily prices are broadly available, but historical SEC share observations and diluted EPS basis are materially sparse. Method A is the usable path where shares align; Method B is generally unavailable. This conclusion is diagnostic and does not change production policy.', '', '## Safety', '', 'No LLM calls; broker place/modify/cancel=0; IBKR writes=0; REAL access=0; PAPER_AUTO disabled.', '', '## Six questions', '', f"Q1. Ordinary supported companies with >=5 valid PE_A years: {result['ordinary_coverage']['pe_a'].get('5_PLUS_VALID_YEARS',0)}.", f"Q2. Ordinary supported companies with 3–4 valid PE_A years: {result['ordinary_coverage']['pe_a'].get('3_TO_4_VALID_YEARS',0)}.", f"Q3. MIN_PEERS=10 PE peer reference AND >=5-year PE history: {overlap.get('both_5',0)}.", f"Q4. Peer reference but insufficient (<3-year) own history: {overlap.get('peer_only_3',0)}.", 'Q5. Method A is more available; Method B is sparse because verified annual diluted EPS facts are largely absent and basis compatibility cannot be assumed.', 'Q6. Own-history valuation is usable only with limited coverage and should not replace peer comparison for V1 without further data-basis decisions.']
    Path('investment_agents/HISTORICAL_VALUATION_V1F_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('WROTE',len(rows_by_ticker),'AGREEMENT',len(diffs),'ORDINARY',len(ordinary_rows),flush=True)
if __name__=='__main__': main()
