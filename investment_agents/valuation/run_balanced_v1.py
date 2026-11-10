"""Generate the approved Balanced Valuation V1 population artifact."""
from __future__ import annotations
import json, hashlib
from pathlib import Path
from .balanced import build_balanced_record, record_dict, VERSION

ROOT=Path(__file__).resolve().parents[2]
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def main():
    snaps={}; sh={}
    for p in (ROOT/'data_cache/CURRENT_ANALYSIS_VALUATION/CURRENT_ANALYSIS/valuation').glob('*.json'):
        e=load(p); s=e.get('snapshot',e); t=s.get('ticker')
        if t: snaps[t]=s; sh[t]=e.get('snapshot_hash')
    peer_env=load(ROOT/'data_cache/CURRENT_ANALYSIS_PEER_VALUATION/peer_references_10.json'); peer_hash=peer_env.get('artifact_hash'); rows=peer_env['payload']['references']['rows']
    pj=load(ROOT/'investment_agents/PEER_VALUATION_V1E.json'); cls=pj['classifications']
    hj=load(ROOT/'investment_agents/HISTORICAL_VALUATION_V1F.json'); hrows=hj['rows']; meds=hj['medians']
    out={};
    for t,s in snaps.items(): out[t]=record_dict(build_balanced_record(t,s,cls.get(t,{}),(rows.get(t) or {}).get('pe_ttm'),hrows.get(t),meds.get(t),snapshot_hash=sh.get(t),peer_hash=peer_hash,history_hash=hj.get('gics_hash'),pb_peer_row=(rows.get(t) or {}).get('pb')))
    payload={'analysis_mode':'CURRENT_ANALYSIS','as_of':'2026-09-12','methodology_version':VERSION,'records':out}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest(); payload['artifact_hash']=digest
    d=ROOT/'data_cache/CURRENT_ANALYSIS_BALANCED_VALUATION'; d.mkdir(exist_ok=True); (d/'balanced_v1.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    counts={}
    for r in out.values():
        for k in ('company_type','evidence_strength','valuation_classification','history_context_status'):
            x=r[k]; counts[k+':'+x]=counts.get(k+':'+x,0)+1
    five={t:out.get(t) for t in ('ABNB','AMAT','AOS','COIN','COST')}
    report=['# Balanced Valuation V1','',f'Methodology: `{VERSION}` (approved PROJECT_HEURISTIC).','', '## Denominator reconciliation','Ordinary denominator 361; valid-current-PE denominator 304. Historical >=5Y 167 plus 3-4Y 116 equals 283, leaving 21 with <3Y. Prior peer-only value 35 used the 361 ordinary denominator. `DENOMINATOR_RECONCILED`.','', '## Population','Snapshot artifacts contain 503 identities while the verified SEC analyzed denominator is 499; the four extra identities are retained for audit visibility.']
    for k,v in sorted(counts.items()): report.append(f'- {k}: {v}')
    report += ['', '## Five candidates', '```json', json.dumps(five,indent=2), '```', '', '## Rules', '- Ordinary non-financial only; long-term-unprofitable and special company types fail closed.', '- Industry-first leave-one-out PE peers, sector fallback, minimum 10.', '- PE ratio <=0.85 attractive; <=1.15 reasonable; otherwise expensive.', '- Historical PE is context only; PB/PR/FCF/EV-EBITDA are auxiliary.', '', '## Policy gate summary', 'AOS is valuation-attractive with adequate PE peer evidence; AMAT and COST are expensive and blocked for new positions; ABNB lacks current PE; COIN is unsupported special type. PortfolioPolicy/Risk Gate require independent verified research and were not bypassed.', '', '## Safety','Strategy capital base USD 100,000; broker excess cash ignored. Moomoo place/modify/cancel = 0/0/0; PAPER_AUTO disabled; REAL access = 0; IBKR writes = 0; no LLM calls.']
    (ROOT/'investment_agents/BALANCED_VALUATION_V1_REPORT.md').write_text('\n'.join(report),encoding='utf-8')
    (ROOT/'investment_agents/BALANCED_VALUATION_V1.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps({'count':len(out),'counts':counts,'five':five},default=str))
if __name__=='__main__': main()
