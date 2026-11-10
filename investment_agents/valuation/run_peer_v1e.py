from __future__ import annotations
import collections, hashlib, json
from pathlib import Path
from .peer import build_references, company_type, percentile, stats, atomic_write_artifact, METRICS, eligible_metric

ROOT=Path('data_cache')
SNAP=ROOT/'CURRENT_ANALYSIS_VALUATION/CURRENT_ANALYSIS/valuation'
SCAN=ROOT/'research_runs/72e523511db44863b9454bba2250de7b/scan_result.json'
PROFIT=ROOT/'research_runs/8c5872fe4f774c9cb9612be5dda4e579/profitability_filter.json'

def load_snapshots():
    out={}; hashes={}
    for p in SNAP.glob('*.json'):
        try:
            x=json.loads(p.read_text())
            if x.get('snapshot',{}).get('ticker'):
                out[x['snapshot']['ticker']]=x['snapshot']; hashes[x['snapshot']['ticker']]=x.get('snapshot_hash')
        except (OSError,ValueError): pass
    return out,hashes

def bin_ratio(x):
    if x is None: return None
    if x <= .8:return '<=0.80'
    if x < .9:return '0.80-0.90'
    if x <= 1.1:return '0.90-1.10'
    if x < 1.2:return '1.10-1.20'
    return '>=1.20'

def main():
    snapshots, hashes=load_snapshots()
    scan=json.loads(SCAN.read_text())['payload']['records']
    scan={str(r['ticker']):r for r in scan if r.get('ticker') and r.get('scan_status')=='ANALYZED'}
    snapshots={t:s for t,s in snapshots.items() if t in scan}
    hashes={t:h for t,h in hashes.items() if t in snapshots}
    profit=json.loads(PROFIT.read_text())['rows']
    excluded={t for t,r in profit.items() if r.get('status')=='LONG_TERM_UNPROFITABLE'}
    insufficient={t for t,r in profit.items() if r.get('status')=='INSUFFICIENT_PROFIT_HISTORY'}
    classifications={}
    for t,s in snapshots.items():
        rec=scan.get(t,{})
        snap=s
        neg_eq=(snap.get('equity') or {}).get('status')=='AVAILABLE' and isinstance((snap.get('equity') or {}).get('value'),(int,float)) and (snap['equity']['value']<=0)
        base_type=company_type(rec.get('sector'),rec.get('industry'),long_loss=t in excluded,insufficient_history=False,negative_equity=neg_eq)
        classifications[t]={'sector':rec.get('sector'),'industry':rec.get('industry'),'company_type':base_type,'profit_history_status':'INSUFFICIENT_PROFIT_HISTORY' if t in insufficient else 'SUFFICIENT_OR_NOT_FLAGGED'}
    thresholds={}; artifacts={}
    for threshold in (5,10,15):
        built=build_references(snapshots,classifications,excluded,threshold); thresholds[str(threshold)]=built
        payload={'as_of':'2026-09-12','threshold':threshold,'valuation_snapshot_hashes':hashes,'gics_source':'scan_result.json','gics_hash':hashlib.sha256(SCAN.read_bytes()).hexdigest(),'references':built}
        artifacts[str(threshold)]=atomic_write_artifact(ROOT/f'CURRENT_ANALYSIS_PEER_VALUATION/peer_references_{threshold}.json',payload)
    coverage={}; sector={}; special={}; ordinary={}
    for threshold,built in thresholds.items():
        q={'PE':collections.Counter(),'PB':collections.Counter()}
        for t,rs in built['rows'].items():
            typ=classifications.get(t,{}).get('company_type','SPECIAL_TYPE_UNKNOWN'); sec=classifications.get(t,{}).get('sector') or 'UNKNOWN'
            special.setdefault(threshold,{}).setdefault(typ,{'count':0,'PE':collections.Counter(),'PB':collections.Counter()})['count']+=1
            sector.setdefault(threshold,{}).setdefault(sec,{'analyzed':0,'PE':collections.Counter(),'PB':collections.Counter()})['analyzed']+=1
            for metric,key in [('pe_ttm','PE'),('pb','PB')]:
                row=rs[metric]; valid=eligible_metric(snapshots[t],metric,excluded)
                if not valid: continue
                status='industry' if row['reference_level']=='GICS_INDUSTRY' else ('sector_fallback' if row['reference_level']=='GICS_SECTOR' else 'no_reference')
                q[key][status]+=1; sector[threshold][sec][key][status]+=1
                special.setdefault(threshold,{}).setdefault(typ,{'count':0,'PE':collections.Counter(),'PB':collections.Counter()})[key][status]+=1
            
        coverage[threshold]={k:dict(v) for k,v in q.items()}
    # type counts, including mutually exclusive diagnostic buckets
    type_counts=collections.Counter(('INSUFFICIENT_PROFIT_HISTORY' if c.get('profit_history_status')=='INSUFFICIENT_PROFIT_HISTORY' else c['company_type']) for c in classifications.values())
    metric_valid={m:[t for t,s in snapshots.items() if eligible_metric(s,m,excluded)] for m in METRICS}
    overlap={'PE_only':len(set(metric_valid['pe_ttm'])-set(metric_valid['pb'])),'PB_only':len(set(metric_valid['pb'])-set(metric_valid['pe_ttm'])),'both':len(set(metric_valid['pe_ttm'])&set(metric_valid['pb'])),'neither':len(snapshots)-len(set(metric_valid['pe_ttm'])|set(metric_valid['pb']))}
    overlap_filtered={'PE_only':len((set(metric_valid['pe_ttm'])-set(metric_valid['pb']))-excluded),'PB_only':len((set(metric_valid['pb'])-set(metric_valid['pe_ttm']))-excluded),'both':len((set(metric_valid['pe_ttm'])&set(metric_valid['pb']))-excluded),'neither':len(snapshots)-len((set(metric_valid['pe_ttm'])|set(metric_valid['pb']))-excluded)-len(excluded)}
    distributions={}; ratio_bins={}
    for m in METRICS:
        vals=[float(snapshots[t][m]['value']) for t in metric_valid[m]]
        distributions[m]={'p1':percentile(vals,.01),'p5':percentile(vals,.05),'p25':percentile(vals,.25),'median':percentile(vals,.5),'p75':percentile(vals,.75),'p95':percentile(vals,.95),'p99':percentile(vals,.99),'max':max(vals) if vals else None,'count':len(vals)}
    group_statistics={}
    for threshold,built in thresholds.items():
        group_statistics[threshold]={}
        for metric in METRICS:
            group_statistics[threshold][metric]={}
            seen=set()
            for t,rowset in built['rows'].items():
                row=rowset[metric]
                if not row.get('reference_level'):
                    continue
                c=classifications[t]
                group_type=c.get('company_type','SPECIAL_TYPE_UNKNOWN')
                group_key=(row['reference_level'], c.get('industry') if row['reference_level']=='GICS_INDUSTRY' else c.get('sector'))
                if group_key in seen:
                    continue
                seen.add(group_key)
                vals=[float(snapshots[o][metric]['value']) for o in row.get('peer_tickers',[]) if eligible_metric(snapshots[o],metric,excluded)]
                group_statistics[threshold][metric][f"{group_type}:{group_key[0]}:{group_key[1]}"]=stats(vals)
    for threshold,built in thresholds.items():
        ratio_bins[threshold]={}
        for m in METRICS:
            ratio_bins[threshold][m]=dict(collections.Counter(bin_ratio(r['ratio']) for r in (x[m] for x in built['rows'].values()) if r.get('ratio') is not None))
    anomalies={}
    for m,d in distributions.items():
        cutoff=d.get('p99')
        anomalies[m]=[{'ticker':t,'value':snapshots[t][m].get('value'),'reason':'ABOVE_P99_DIAGNOSTIC_ONLY'} for t in metric_valid[m] if cutoff is not None and snapshots[t][m].get('value')>cutoff]
    five={t:{'sector':classifications.get(t,{}).get('sector'),'industry':classifications.get(t,{}).get('industry'),'thresholds':thresholds[k]['rows'].get(t)} for t in ['ABNB','AMAT','AOS','COIN','COST'] for k in []}
    five={}
    for t in ['ABNB','AMAT','AOS','COIN','COST']:
        five[t]={'sector':classifications.get(t,{}).get('sector'),'industry':classifications.get(t,{}).get('industry'),'pe':snapshots.get(t,{}).get('pe_ttm'),'pb':snapshots.get(t,{}).get('pb'),'thresholds':{k:thresholds[k]['rows'].get(t) for k in thresholds}}
    ordinary=[t for t,c in classifications.items() if c['company_type']=='ORDINARY_NON_FINANCIAL']
    ordinary_coverage={}
    for k,b in thresholds.items():
        ordinary_coverage[k]={m:dict(collections.Counter(('industry' if b['rows'][t][m]['reference_level']=='GICS_INDUSTRY' else 'sector_fallback' if b['rows'][t][m]['reference_level']=='GICS_SECTOR' else 'no_reference') for t in ordinary if eligible_metric(snapshots[t],m,excluded))) for m in METRICS}
    result={'status':'PEER_VALUATION_V1E_READY','input_coverage':json.loads((Path('investment_agents/CURRENT_VALUATION_DATA_V1D.json')).read_text())['coverage'],'peer_universe':{'analyzed':len(snapshots),'excluded_long_term_unprofitable':len(excluded),'after_filter':len(snapshots)-len(excluded),'insufficient_profit_history':len(insufficient)},'excluded_tickers':sorted(excluded),'company_type_buckets':dict(type_counts),'classifications':classifications,'thresholds':coverage,'threshold_artifact_hashes':artifacts,'sector_coverage':sector,'special_sector_coverage':special,'five_names':five,'metric_overlap':{'full':overlap,'after_filter':overlap_filtered},'ordinary_non_financial':{'count':len(ordinary),'coverage':ordinary_coverage},'population_distributions':distributions,'peer_group_statistics':group_statistics,'ratio_bins':ratio_bins,'data_quality_anomalies':anomalies,'gics_hash':hashlib.sha256(SCAN.read_bytes()).hexdigest()}
    Path('investment_agents/PEER_VALUATION_V1E.json').write_text(json.dumps(result,indent=2,sort_keys=True,default=str),encoding='utf-8')
    lines=['# PEER VALUATION V1E','', 'Deterministic descriptive peer references only. No valuation decision or policy threshold is implemented.', '', '## Universe', '', f"- Analyzed: {len(snapshots)}; long-term-unprofitable excluded: {len(excluded)}; peer universe after filter: {len(snapshots)-len(excluded)}; insufficient profit history reported separately: {len(insufficient)}.", f"- Excluded tickers: {', '.join(sorted(excluded))}", '', '## Company-type buckets', '']
    lines += [f"- {k}: {v}" for k,v in sorted(type_counts.items())]
    lines += ['', '## Threshold simulations', '', '|Threshold|Metric|Industry reference|Sector fallback|No reference|Valid-metric denominator|','|---:|---|---:|---:|---:|---:|']
    for k,c in coverage.items():
        for metric,label in [('PE','PE'),('PB','PB')]:
            den=len(metric_valid['pe_ttm' if metric=='PE' else 'pb'])-len([t for t in excluded if t in metric_valid['pe_ttm' if metric=='PE' else 'pb']])
            q=c[metric]; lines.append(f"|{k}|{label}|{q.get('industry',0)}|{q.get('sector_fallback',0)}|{q.get('no_reference',0)}|{den}|")
    lines += ['', '### Ordinary-company PE reference percentages', '']
    for k in ('5','10','15'):
        q=ordinary_coverage[k]['pe_ttm']; den=sum(q.values()); got=q.get('industry',0)+q.get('sector_fallback',0)
        lines.append(f"- MIN_PEERS={k}: {got}/{den} = {(100*got/den if den else 0):.2f}% (industry {q.get('industry',0)}, sector fallback {q.get('sector_fallback',0)}, no reference {q.get('no_reference',0)}).")
    lines += ['', '### Sector threshold effect (PE reference counts)', '']
    for sec in sorted(sector.get('5',{})):
        vals=[]
        for k in ('5','10','15'):
            q=sector[k].get(sec,{}).get('PE',{}); vals.append(f"{k}:I{q.get('industry',0)}/S{q.get('sector_fallback',0)}/N{q.get('no_reference',0)}")
        lines.append(f"- {sec}: " + ', '.join(vals))
    lines += ['', '## Five-name validation', '', '|Ticker|Sector|Industry|PE value|PB value|Threshold|PE level/count/median|PB level/count/median|','|---|---|---|---:|---:|---:|---|---|']
    for t,v in five.items():
        for k in ('5','10','15'):
            pe=v['thresholds'][k]['pe_ttm']; pb=v['thresholds'][k]['pb']; pv=v['pe']['value'] if v['pe'].get('status')=='AVAILABLE' else v['pe'].get('reason'); bv=v['pb']['value'] if v['pb'].get('status')=='AVAILABLE' else v['pb'].get('reason')
            lines.append(f"|{t}|{v['sector']}|{v['industry']}|{pv}|{bv}|{k}|{pe.get('reference_level')}/{pe.get('peer_count')}/{pe.get('reference_value')}|{pb.get('reference_level')}/{pb.get('peer_count')}/{pb.get('reference_value')}|")
    lines += ['', '## PE/PB overlap', '', f"- Full analyzed population: {overlap}", f"- After long-term-unprofitable exclusion: {overlap_filtered}", '', '## Population distributions', '']
    for m,d in distributions.items(): lines.append(f"- {m}: {d}")
    lines += ['', '## User decision support', '', f"Q1. At MIN_PEERS=5, ordinary-company PE industry/fallback references: {coverage['5']['PE']} (ordinary subset: {ordinary_coverage['5']['pe_ttm']}).", f"Q2. At MIN_PEERS=10: {coverage['10']['PE']} (ordinary subset: {ordinary_coverage['10']['pe_ttm']}).", f"Q3. At MIN_PEERS=15: {coverage['15']['PE']} (ordinary subset: {ordinary_coverage['15']['pe_ttm']}).", f"Q4. Companies with both PE and PB after filter: {overlap_filtered['both']}.", 'Q5. Sector changes are quantified in the JSON sector_coverage table; increasing the threshold reduces references in sectors with smaller industries first.', 'Q6. Industry-first plus sector fallback provides measurable descriptive coverage; whether it is broad enough for production Balanced V1 remains a user methodology decision. No policy was changed.', '', '## Safety and tests', '', '- No LLM or broker calls. place=0, modify=0, cancel=0; IBKR writes=0; REAL access=0; PAPER_AUTO=disabled.']
    Path('investment_agents/PEER_VALUATION_V1E_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('WROTE',len(snapshots),'EXCLUDED',len(excluded),'HASHES',artifacts)

if __name__=='__main__': main()
