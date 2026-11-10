import unittest
from investment_agents.committee.agent_v2 import build_claim_menu, normalize_agent_v2
from investment_agents.committee.claims import CanonicalClaim

class AgentV2Tests(unittest.TestCase):
    def setUp(self):
        self.claims=(CanonicalClaim('claim:A:profitability:revenue','A','PROFITABILITY','A revenue = 1',('source:A:x:revenue',),('revenue',),(),(None,),'h'), CanonicalClaim('claim:A:valuation:pe','A','VALUATION','A PE = 10',('source:A:x:pe',),('pe',),(),(None,),'h'))
        self.menu=build_claim_menu(self.claims)
    def test_valid_alias_and_dedupe(self):
        r=normalize_agent_v2({'stance':'MIXED','supporting_claim_refs':['C001','C001'],'risk_claim_refs':[],'interpretations':[{'statement':'x','supporting_claim_refs':['C001']}]},ticker='A',run_id='r',claims=self.claims,menu=self.menu)
        self.assertEqual(r['status'],'VALID'); self.assertEqual(len(r['supporting_claim_ids']),1)
    def test_unknown_alias(self):
        r=normalize_agent_v2({'stance':'MIXED','supporting_claim_refs':['C999']},ticker='A',run_id='r',claims=self.claims,menu=self.menu)
        self.assertEqual(r['status'],'INVALID')
    def test_abstention_valid(self):
        r=normalize_agent_v2({'stance':'INSUFFICIENT_EVIDENCE','supporting_claim_refs':[]},ticker='A',run_id='r',claims=self.claims,menu=self.menu)
        self.assertEqual(r['status'],'VALID')
    def test_unsupported_interpretation(self):
        r=normalize_agent_v2({'stance':'MIXED','supporting_claim_refs':['C001'],'interpretations':[{'statement':'x','supporting_claim_refs':[]}]},ticker='A',run_id='r',claims=self.claims,menu=self.menu)
        self.assertEqual(r['status'],'INVALID')
    def test_wrong_ticker_and_domain(self):
        other=CanonicalClaim('claim:B:x:y','B','PROFITABILITY','B fact',(),(),(),(), 'h')
        menu=build_claim_menu((other,)+self.claims)
        r=normalize_agent_v2({'stance':'MIXED','supporting_claim_refs':['C001'],'interpretations':[{'statement':'x','domain':'VALUATION','supporting_claim_refs':['C002']}]},ticker='A',run_id='r',claims=(other,)+self.claims,menu=menu)
        self.assertEqual(r['status'],'INVALID')
        self.assertIn('AGENT_CLAIM_WRONG_TICKER',r['reason_codes'])
if __name__=='__main__': unittest.main()
