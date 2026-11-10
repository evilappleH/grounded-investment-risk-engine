import unittest
from investment_agents.valuation.balanced import build_balanced_record
from investment_agents.valuation.live import valuation_allows_new_position

def snap(pe=10, ni=100):
    return {'as_of':'2026-01-01','pe_ttm':{'status':'AVAILABLE','value':pe},'pb':{'status':'UNAVAILABLE','value':None},'pr':{'status':'UNAVAILABLE'},'net_income_ttm':{'status':'AVAILABLE','value':ni},'fcf_yield':{'status':'UNAVAILABLE'}}
class BalancedTests(unittest.TestCase):
    def row(self, med): return {'reference_value':med,'peer_count':10,'reference_level':'GICS_INDUSTRY','peer_tickers':['X'+str(i) for i in range(10)]}
    def test_boundaries(self):
        for pe, expected in [(8.5,'VALUATION_ATTRACTIVE'),(8.50001,'VALUATION_REASONABLE'),(11.5,'VALUATION_REASONABLE'),(11.50001,'VALUATION_EXPENSIVE')]:
            r=build_balanced_record('X',snap(pe),{'company_type':'ORDINARY_NON_FINANCIAL'},self.row(10),{'pe_a_count':0},{})
            self.assertEqual(r.valuation_classification,expected)
    def test_special_and_history(self):
        r=build_balanced_record('X',snap(),{'company_type':'REIT'},self.row(10),{'pe_a_count':5},{'PE':{'status':'5Y_REFERENCE','value':12}})
        self.assertEqual(r.valuation_classification,'VALUATION_MODEL_NOT_SUPPORTED_V1')
        self.assertEqual(r.history_context_status,'ROBUST_HISTORY_CONTEXT')
    def test_missing_history_nonfatal(self):
        r=build_balanced_record('X',snap(),{'company_type':'ORDINARY_NON_FINANCIAL'},self.row(10),{'pe_a_count':0},{})
        self.assertEqual(r.evidence_strength,'VALUATION_EVIDENCE_ADEQUATE')
    def test_long_loss(self):
        r=build_balanced_record('X',snap(),{'company_type':'LONG_TERM_UNPROFITABLE'},self.row(10),{}, {})
        self.assertEqual(r.valuation_classification,'EXCLUDED_FROM_BALANCED_UNIVERSE')
    def test_policy_bridge(self):
        base={'company_type':'ORDINARY_NON_FINANCIAL','evidence_strength':'VALUATION_EVIDENCE_ADEQUATE'}
        self.assertTrue(valuation_allows_new_position({**base,'valuation_classification':'VALUATION_ATTRACTIVE'})[0])
        self.assertEqual(valuation_allows_new_position({**base,'valuation_classification':'VALUATION_EXPENSIVE'})[1],'VALUATION_TOO_EXPENSIVE_FOR_NEW_POSITION_V1')
if __name__=='__main__': unittest.main()
