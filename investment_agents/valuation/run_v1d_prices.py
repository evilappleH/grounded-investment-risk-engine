from __future__ import annotations
import concurrent.futures, datetime, json, time
from pathlib import Path
from investment_agents.data.tradingagents_adapter import TradingAgentsMarketDataAdapter

def main() -> None:
    payload=json.loads(Path('data_cache/research_runs/72e523511db44863b9454bba2250de7b/scan_result.json').read_text())['payload']
    tickers=[str(r['ticker']) for r in payload['records'] if r.get('ticker') and r.get('scan_status') == 'ANALYZED']
    end=datetime.date(2026,9,12)
    def one(t):
        try:
            h=TradingAgentsMarketDataAdapter().get_daily_prices(t,end=end)
            bars=[b for b in h.bars if b.close is not None and b.session_date <= end]
            if not bars: return t, {'status':'UNAVAILABLE','reason':'EMPTY_RESPONSE','provider':'tradingagents:yfinance'}
            b=max(bars,key=lambda x:x.session_date)
            return t, {'status':'AVAILABLE','price':b.close,'price_date':b.session_date.isoformat(),'currency':'USD','source':'tradingagents:yfinance','source_type':'PRIMARY_DAILY_PROVIDER'}
        except Exception as e:
            msg=str(e).lower()
            reason='YAHOO_RATE_LIMIT' if 'rate' in msg or '429' in msg else ('YAHOO_EMPTY_RESPONSE' if 'no rows' in msg or 'empty' in msg else 'YAHOO_NETWORK_FAILURE')
            return t, {'status':'UNAVAILABLE','reason':reason,'provider':'tradingagents:yfinance','exception_class':type(e).__name__}
    out={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for i,(t,r) in enumerate(ex.map(one,tickers),1):
            out[t]=r
            if i % 25 == 0: print('PROCESSED',i,flush=True)
    Path('data_cache/v1d_prices.json').write_text(json.dumps(out,sort_keys=True),encoding='utf-8')
    print('TOTAL',len(out),'AVAILABLE',sum(v['status']=='AVAILABLE' for v in out.values()),'UNAVAILABLE',sum(v['status']!='AVAILABLE' for v in out.values()),flush=True)

if __name__=='__main__': main()
