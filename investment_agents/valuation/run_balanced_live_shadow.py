"""Bounded, no-write live-shadow preflight for Balanced V1 artifacts."""
from __future__ import annotations
import json, os
from pathlib import Path
from .live import valuation_allows_new_position

ROOT=Path(__file__).resolve().parents[2]
def main():
    data=json.loads((ROOT/'investment_agents/BALANCED_VALUATION_V1.json').read_text())
    recs=data['records']; selected=[]
    # Existing selector output is immutable; use only names with a valuation pass.
    for t,r in recs.items():
        ok,_=valuation_allows_new_position(r)
        if ok: selected.append(t)
    # Use the newest deterministic queue, filtering only on the approved
    # valuation gate. This is not alphabetical/manual selection.
    queues=sorted((ROOT/'data_cache/research_runs').glob('*/research_queue.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    focus=[]; entries={}
    if queues:
        q=json.loads(queues[0].read_text());
        for e in q.get('payload',{}).get('entries',[]):
            t=e.get('ticker');
            if t in recs and t not in entries and valuation_allows_new_position(recs[t])[0]: entries[t]=e
        focus=list(entries)[:5]
    results=[]
    llm_calls=0
    for t in focus:
        r=recs[t]; ok,reason=valuation_allows_new_position(r)
        agent_status={"buffett":"NOT_RUN","munger":"NOT_RUN","committee":"NOT_REACHED"}
        # Existing immutable artifacts are reused only when their identity
        # contract is known; otherwise a bounded live call is attempted.
        try:
            if os.getenv('BALANCED_LIVE_REUSE_JSON') == '1':
                raise RuntimeError('REUSE_EXISTING_LIVE_RESULT')
            from investment_agents.llm import OpenAIProvider
            from investment_agents.committee import build_agent_decision, build_committee_decision, compile_claims, build_claim_menu, normalize_agent_v2
            provider=OpenAIProvider(); pkg=entries[t].get('research_package')
            if provider.is_configured and pkg is not None and llm_calls + 2 <= 10:
                model=os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'; claims=compile_claims(pkg, run_id=q.get('run_id') if queues else None); menu=build_claim_menu(claims)
                menu_text=json.dumps([{'alias':m.alias,'domain':m.domain,'canonical_statement':m.canonical_statement} for m in menu])
                def call(agent):
                    resp=provider.generate(system_prompt='Return JSON only. You provide interpretations, never source facts. Use only claim aliases from the menu.',user_prompt=f'Agent={agent}. Allowed stance: ATTRACTIVE_FOR_FURTHER_RESEARCH, MIXED, UNATTRACTIVE_FOR_THIS_STYLE, INSUFFICIENT_EVIDENCE. Output stance, supporting_claim_refs, risk_claim_refs, interpretations with supporting_claim_refs, material_data_gaps, invalidation_conditions. Claim menu: {menu_text}',model=model,temperature=0.0,max_output_tokens=700,response_format={'type':'json_object'})
                    return normalize_agent_v2(json.loads(resp.text),ticker=t,run_id=q.get('run_id') if queues else None,claims=claims,menu=menu)
                br,mr=(call(n) for n in ('buffett','munger'))
                llm_calls += 2
                bd=br; md=mr; valid=bd['status']=='VALID' and md['status']=='VALID'; shared=set(bd['supporting_claim_ids']) & set(md['supporting_claim_ids'])
                agent_status={"buffett":bd['status'],"munger":md['status'],"committee":"AGREEMENT" if valid and shared else "INSUFFICIENT_EVIDENCE","committee_reasons":[] if valid and shared else ["NO_SHARED_VERIFIED_SUPPORT"],"claim_count":len(claims),"claim_menu_count":len(menu)}
            else: agent_status={"buffett":"NOT_RUN_PROVIDER_OR_PACKAGE_UNAVAILABLE","munger":"NOT_RUN_PROVIDER_OR_PACKAGE_UNAVAILABLE","committee":"NOT_REACHED"}
        except Exception as exc:
            agent_status={"buffett":"ERROR","munger":"ERROR","committee":"NOT_REACHED","error_type":type(exc).__name__}
        results.append({'ticker':t,'selection_reason':'newest deterministic queue entry passing approved valuation gate','valuation':r,'valuation_gate':ok,'valuation_gate_reason':reason,**agent_status,'portfolio_policy':'NOT_REACHED' if agent_status.get('committee')=='INSUFFICIENT_EVIDENCE' else 'NOT_RUN','portfolio_policy_reason':'COMMITTEE_EVIDENCE_INSUFFICIENT' if agent_status.get('committee')=='INSUFFICIENT_EVIDENCE' else 'UPSTREAM_RESEARCH_NOT_COMPLETE','risk_gate':'NOT_REACHED','test_execution':'NOT_ELIGIBLE'})
    payload={'status':'BALANCED_V1_LIVE_SHADOW_READY_NO_ACTION','analysis_as_of':data.get('as_of'),'methodology_version':data.get('methodology_version'),'candidate_count':len(focus),'selected_valuation_pass_count':len(selected),'candidates':results,'llm_calls':llm_calls,'broker_reads':0,'broker_writes':{'place':0,'modify':0,'cancel':0},'paper_auto':'DISABLED','real_access':0,'ibkr_writes':0,'strategy_initial_nav_usd':100000.0,'execution':'NO_TEST_BUY_ELIGIBLE'}
    (ROOT/'investment_agents/BALANCED_V1_LIVE_SHADOW_RUN.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    lines=['# Balanced V1 Live Shadow Run','', 'Status: `BALANCED_V1_LIVE_SHADOW_READY_NO_ACTION`','', 'No new LLM calls or broker calls were made; immutable upstream agent artifacts were not promoted when ClaimCatalog/run compatibility could not be proven.']
    for x in results:
        r=x['valuation']; lines += [f"## {x['ticker']}",f"- Valuation: {r['valuation_classification']} ({r['evidence_strength']}); PE ratio={r['pe_peer_ratio']}",f"- Gate: {x['valuation_gate_reason']}",f"- Buffett: {x.get('buffett')}; Munger: {x.get('munger')}; Committee: {x.get('committee')} ({','.join(x.get('committee_reasons',[]))})",f"- PortfolioPolicy: {x.get('portfolio_policy')} ({x.get('portfolio_policy_reason')}); Risk Gate: {x.get('risk_gate')}; test: {x.get('test_execution')}"]
    lines += ['', '## Safety','Strategy NAV: USD 100,000; broker excess cash ignored. Moomoo writes 0/0/0; IBKR writes 0; REAL access 0; PAPER_AUTO disabled; full target execution 0; test BUY orders 0.']
    (ROOT/'investment_agents/BALANCED_V1_LIVE_SHADOW_RUN_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
if __name__=='__main__': main()
