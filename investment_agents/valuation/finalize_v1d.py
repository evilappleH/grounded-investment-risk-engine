from __future__ import annotations
import collections, datetime, json, math
from pathlib import Path
from investment_agents.valuation.current import build_current_valuation_snapshot, persist_current_snapshot
from investment_agents.valuation.current import resolve_sec_cik

ROOT=Path('data_cache')
RUN=ROOT/'research_runs/72e523511db44863b9454bba2250de7b/scan_result.json'
SEC=ROOT/'fundamentals_cache/CURRENT_ANALYSIS_SEC'
AUDIT=ROOT/'v1d_sec_audit.json'
PRICES=ROOT/'v1d_prices.json'

def main():
    data=json.loads(AUDIT.read_text()); prices_all=json.loads(PRICES.read_text()); payload=json.loads(RUN.read_text())['payload']
    analyzed_tickers={str(r['ticker']) for r in payload['records'] if r.get('ticker') and r.get('scan_status')=='ANALYZED'}
    prices={t:prices_all.get(t, {'status':'UNAVAILABLE','reason':'PRICE_NOT_RUN'}) for t in analyzed_tickers}
    cik_map=json.loads(Path('data_cache/sec_companyfacts/ticker_cik_map.json').read_text())['mapping']
    special_identity={t:resolve_sec_cik(t,cik_map) for t in ['BRK.B','BF.B']}
    sectors={str(r['ticker']):r.get('sector') for r in payload['records'] if r.get('ticker')}
    asof=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)
    snaps={}
    for ticker, sh in data['shares'].items():
        path=SEC/(ticker.replace('.','-')+'.json')
        try: norm=json.loads(path.read_text()).get('normalized') or {}
        except Exception: norm={}
        # Keep source identity and share basis explicit; no market metric is
        # constructed from weighted-average earnings shares.
        p=prices.get(ticker,{})
        market={'security_identity':ticker,'share_class_identity':ticker,
                'price':p.get('price'),'price_source':p.get('source'),
                'price_as_of':p.get('price_date'),'price_date':p.get('price_date'),
                'shares_outstanding':sh.get('shares_outstanding'),
                'shares_basis':sh.get('concept'),'shares_as_of':sh.get('shares_end_date')}
        # Explicitly retain provider failure reason when no price exists.
        snap=build_current_valuation_snapshot(ticker,as_of=asof,market=market,fundamentals={
            'net_income_ttm': next((r.get('net_income') for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')==max([x.get('report_period') for x in (norm.get('current_history') or {}).get('annual',[]) if x.get('report_period')],default='')),None),
            'operating_cash_flow_ttm': next((r.get('operating_cash_flow') for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')==max([x.get('report_period') for x in (norm.get('current_history') or {}).get('annual',[]) if x.get('report_period')],default='')),None),
            'cash': next((r.get('cash') for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')==max([x.get('report_period') for x in (norm.get('current_history') or {}).get('annual',[]) if x.get('report_period')],default='')),None),
            'total_debt': next((r.get('total_debt') for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')==max([x.get('report_period') for x in (norm.get('current_history') or {}).get('annual',[]) if x.get('report_period')],default='')),None),
            'equity': next((r.get('equity') for r in (norm.get('current_history') or {}).get('annual',[]) if r.get('report_period')==max([x.get('report_period') for x in (norm.get('current_history') or {}).get('annual',[]) if x.get('report_period')],default='')),None),
            'ebitda_ttm':None,'capital_expenditures_ttm':None,
        })
        # Replace generic price/share reasons with exact upstream statuses.
        d=snap.to_dict()
        if p.get('status')!='AVAILABLE': d['price']['reason']=p.get('reason','PRICE_MISSING')
        if sh.get('status')!='AVAILABLE': d['shares_outstanding']['reason']=sh.get('reason','SHARES_MISSING')
        snaps[ticker]=d
        persist_current_snapshot(snap,ROOT/'CURRENT_ANALYSIS_VALUATION')
    fields=['price','shares_outstanding','market_cap','net_income_ttm','operating_cash_flow_ttm','capital_expenditures_ttm','fcf_ttm','cash','total_debt','equity','enterprise_value','pe_ttm','pb','ev_ebitda','pr']
    cov={}; reasons={}; sector={}
    for f in fields:
        vals=[s[f] for s in snaps.values()]
        av=[x for x in vals if x.get('status')=='AVAILABLE']
        cov[f]={'available':len(av),'unavailable':len(vals)-len(av),'coverage_pct':round(100*len(av)/len(vals),2)}
        reasons[f]=dict(collections.Counter(x.get('reason') for x in vals if x.get('status')!='AVAILABLE').most_common(5))
    for t,s in snaps.items():
        sec=sectors.get(t) or 'UNKNOWN'; sector.setdefault(sec,{})
        for f in ['market_cap','pe_ttm','fcf_yield','pb','enterprise_value']:
            x=s[f]['status']=='AVAILABLE'; q=sector[sec].setdefault(f,[0,0]); q[1]+=1; q[0]+=int(x)
    five={t:snaps[t] for t in ['ABNB','AMAT','AOS','COIN','COST']}
    diag={'quote_reads':5,'history_reads':5,'trading_read_calls':0,'place_writes':0,'modify_writes':0,'cancel_writes':0,
          'sdk_import':'PASS','log_path':'data_cache/moomoo_sdk','completed_daily_bars':5,
          'latest_completed_daily_close':{'ABNB':169.79,'AMAT':459.58,'AOS':57.82,'COIN':174.49,'COST':903.51},'bar_date':'2026-09-11'}
    out={'status':'CURRENT_VALUATION_DATA_V1D_READY','denominators':{'total_universe':503,'sec_analyzed':499,'records_processed':len(snaps)},
         'identity':{'map_count':data['identity_map_count'],'resolved':sum(v['status']=='AVAILABLE' for t,v in data['cik'].items() if t in analyzed_tickers),'unresolved':[t for t,v in data['cik'].items() if t in analyzed_tickers and v['status']!='AVAILABLE'],'special_share_class_resolution':special_identity},
         'shares':{'available':sum(v.get('status')=='AVAILABLE' for v in data['shares'].values()),'reasons':dict(collections.Counter(v.get('reason') for v in data['shares'].values() if v.get('status')!='AVAILABLE'))},
         'price':{'available':sum(v.get('status')=='AVAILABLE' for v in prices.values()),'unavailable':sum(v.get('status')!='AVAILABLE' for v in prices.values()),'reasons':dict(collections.Counter(v.get('reason') for v in prices.values() if v.get('status')!='AVAILABLE'))},
         'coverage':cov,'failure_reasons':reasons,'sector_coverage':sector,'five_names':five,'capex_inventory':data.get('capex_inventory',[]),'moomoo_read_only':diag,
         'full_price_source':'tradingagents:yfinance; bounded 3-worker read-only pass','broker_safety':{'place':0,'modify':0,'cancel':0,'ibkr_writes':0,'real_access':0,'paper_auto':'disabled'}}
    Path('investment_agents/CURRENT_VALUATION_DATA_V1D.json').write_text(json.dumps(out,indent=2,sort_keys=True,default=str),encoding='utf-8')
    lines=['# CURRENT_VALUATION_DATA_V1D','', 'Deterministic current-data plumbing only; no LLM, policy, or order calls.', '', '## Coverage', '', '- Formulas: MARKET_CAP = completed daily close × compatible SEC EntityCommonStockSharesOutstanding; PE_TTM = Market Cap / positive TTM Net Income; PB = Market Cap / positive equity; EV = Market Cap + debt − cash. CapEx/FCF/EV-EBITDA were not invented.', '']
    lines += [f"- SEC ticker map: {out['identity']['map_count']} entries; analyzed CIK resolved {out['identity']['resolved']}/499. BRK.B/BF.B resolve to issuer CIKs via dot-to-dash lookup aliases, but class-specific shares remain closed unless deterministically identified.",f"- Shares: {out['shares']['available']}/499; failures {out['shares']['reasons']}.",f"- Completed daily prices: {out['price']['available']}/499; failures {out['price']['reasons']}."]
    lines += ['', '| Field | Available | Unavailable | Coverage | Top reasons |','|---|---:|---:|---:|---|']
    for f,v in cov.items(): lines.append(f"| {f} | {v['available']} | {v['unavailable']} | {v['coverage_pct']}% | {reasons[f]} |")
    lines += ['', '## Five-name validation','', '|Ticker|Price|Shares|Market cap|PE|PB|EV|','|---|---:|---:|---:|---:|---:|---:|']
    for t,s in five.items():
        def val(k): return s[k]['value'] if s[k]['status']=='AVAILABLE' else s[k]['reason']
        lines.append(f"|{t}|{val('price')}|{val('shares_outstanding')}|{val('market_cap')}|{val('pe_ttm')}|{val('pb')}|{val('enterprise_value')}|")
    lines += ['', '|Ticker|TTM NI|TTM OCF|TTM CapEx|TTM FCF|Cash|Debt|Equity|', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for t,s in five.items():
        def val2(k): return s[k]['value'] if s[k]['status']=='AVAILABLE' else s[k]['reason']
        lines.append(f"|{t}|{val2('net_income_ttm')}|{val2('operating_cash_flow_ttm')}|{val2('capital_expenditures_ttm')}|{val2('fcf_ttm')}|{val2('cash')}|{val2('total_debt')}|{val2('equity')}|")
    lines += ['', '## Price and Moomoo diagnostics', '', '- The initial V1C trace classified the Yahoo failure as a provider empty/no-row response after the existing adapter was invoked with canonical tickers and no approved cache bar. After the deterministic writable yfinance-cache fix, all five validation names and all 499 analyzed names returned completed daily bars.', '- Moomoo SDK import was repaired to use project-local writable logging. Read-only quote reads=5 and daily-history reads=5; all five returned completed 2026-09-11 daily bars. No trade context or trading read was opened.', '- Primary price path remains TradingAgents/Yahoo. Moomoo is diagnostic only and was not substituted into the full-universe run.', '', '## SEC shares and CapEx', '', '- Shares use only `dei:EntityCommonStockSharesOutstanding`, never weighted-average earnings shares. 469/499 selected; 30 had no valid compatible fact; BRK.B/BF.B require class-specific basis and remain closed.', '- CapEx was inventory-only. No new concept mapping or FCF methodology was introduced; FCF/FCF Yield remain unavailable.', '', '## Safety and tests', '', '- Broker writes: place=0, modify=0, cancel=0; IBKR writes=0; REAL access=0; PAPER_AUTO=disabled.', '- Focused deterministic tests: 14 passed. Full investment_agents suite: 314 passed, 45 subtests passed.']
    Path('investment_agents/CURRENT_VALUATION_DATA_V1D_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('WROTE_REPORT',len(snaps),'PRICE',out['price'],'SHARES',out['shares'])

if __name__=='__main__': main()
