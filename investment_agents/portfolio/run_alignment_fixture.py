from datetime import datetime, timezone, timedelta
from investment_agents.portfolio import *
from investment_agents.research import VerifiedRunManifest
from investment_agents.risk import RiskGate, Quote, RiskContext, KillSwitch
now=datetime(2026,9,12,12,tzinfo=timezone.utc); mf=VerifiedRunManifest('alignment-run','m','a'*64,now.isoformat(),4)
for t,cl in [('AOS','VALUATION_ATTRACTIVE'),('ABT','VALUATION_REASONABLE'),('AMD','VALUATION_REASONABLE'),('LNT','VALUATION_REASONABLE')]:
 r=ResearchEligibility(f'US:EQUITY:{t}','alignment-run','a'*64,'m',now-timedelta(minutes=2),now-timedelta(minutes=2),'QUALIFIED','PARTIAL','COMPLETE',valuation_evidence_status='VALUATION_EVIDENCE_ADEQUATE',committee_status='AGREEMENT',committee_evidence_sufficiency='SUFFICIENT',committee_claim_ids=('claim:'+t+':profitability:revenue',),valuation_classification=cl,research_lane='BALANCED')
 target=AllocationTarget(InstrumentId(t,InstrumentType.EQUITY),.1,'Industrials',r); state=PortfolioState(now-timedelta(seconds=5),100000,100000,()); p=PortfolioEngine().propose(state,(target,),verified_manifest=mf,proposal_id='align-'+t,decision_time=now); q=Quote(target.instrument.canonical_id,100,now-timedelta(seconds=1)); risk=RiskGate().evaluate(p,state,{target.instrument.canonical_id:q},context=RiskContext(environment='SIMULATOR'),kill_switch=KillSwitch(False),checked_at=now); print(t,p.status.value,list(p.rejection_reasons),risk.status.value,list(risk.rejection_reasons))
