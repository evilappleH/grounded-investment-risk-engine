from __future__ import annotations
import concurrent.futures, datetime, json
from pathlib import Path
from investment_agents.data.tradingagents_adapter import TradingAgentsMarketDataAdapter

def main():
    records=json.loads(Path('data_cache/research_runs/72e523511db44863b9454bba2250de7b/scan_result.json').read_text())['payload']['records']
    tickers=[str(r['ticker']) for r in records if r.get('ticker') and r.get('scan_status')=='ANALYZED']
    end=datetime.date(2026,9,12)
    def one(t):
        try:
            h=TradingAgentsMarketDataAdapter().get_daily_prices(t,end=end)
            bars=[{'date':b.session_date.isoformat(),'close':b.close} for b in h.bars if b.close is not None]
            return t, {'status':'AVAILABLE' if bars else 'UNAVAILABLE','bars':bars,'source':'tradingagents:yfinance','price_basis':'AUTO_ADJUST_TRUE_SPLIT_DIVIDEND_ADJUSTED'}
        except Exception as e:
            return t, {'status':'UNAVAILABLE','reason':'HISTORICAL_PRICE_MISSING','exception_class':type(e).__name__}
    out={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for i,(t,v) in enumerate(ex.map(one,tickers),1):
            out[t]=v
            if i%50==0: print('PROCESSED',i,flush=True)
    Path('data_cache/v1f_prices_history.json').write_text(json.dumps(out,sort_keys=True),encoding='utf-8')
    print('TOTAL',len(out),'AVAILABLE',sum(v['status']=='AVAILABLE' for v in out.values()),flush=True)
if __name__=='__main__': main()
