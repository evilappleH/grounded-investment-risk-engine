import unittest
from investment_agents.portfolio.pre_auto_selector import select_pre_auto_candidate

def c(t, **kw):
    x={'ticker':t,'portfolio_policy':'ELIGIBLE','risk_gate':'PASS','committee_status':'AGREEMENT','valuation_classification':'VALUATION_REASONABLE','reference_price':100}; x.update(kw); return x
class SelectorTests(unittest.TestCase):
    def test_over_500_excluded(self): self.assertIsNone(select_pre_auto_candidate([c('AMD',reference_price=516)])["selected"])
    def test_agreement_beats_partial(self): self.assertEqual(select_pre_auto_candidate([c('A',committee_status='PARTIAL_AGREEMENT'),c('B')])['selected'],'B')
    def test_attractive_beats_reasonable(self): self.assertEqual(select_pre_auto_candidate([c('A'),c('B',valuation_classification='VALUATION_ATTRACTIVE')])['selected'],'B')
    def test_lower_notional_tie_break(self): self.assertEqual(select_pre_auto_candidate([c('A',reference_price=90),c('B',reference_price=80)])['selected'],'B')
    def test_exact_tie_fails_closed(self): self.assertEqual(select_pre_auto_candidate([c('A'),c('B')])['status'],'PRE_AUTO_SELECTION_AMBIGUOUS')
    def test_metadata_is_test_only(self):
        r=select_pre_auto_candidate([c('A')]); self.assertEqual(r['selection_contract'],'PRE_AUTO_TEST_SELECTOR_V1'); self.assertTrue(r['not_investment_ranking'])
if __name__=='__main__': unittest.main()
