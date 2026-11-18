import unittest
from verifin.model import OutputContractError, validated_output

class RoleWire(unittest.TestCase):
    tools={'tools':[{'name':'search_documents'}]}
    def test_registered_name_and_one_explicit_argument_container(self):
        for key in ['arguments','parameters']:
            original={'action':'search_documents',key:{'query':'operating income'},'reason':'Read the release'}
            out=validated_output('planner',original,self.tools)
            self.assertEqual((out['action'],out['tool'],out['arguments']),('tool','search_documents',{'query':'operating income'}))
            self.assertEqual(original['action'],'search_documents')
    def test_ambiguous_calls_not_silently_repaired(self):
        for extra in ({'query':'income'},{'arguments':{},'parameters':{}},{'tool':'something_else','arguments':{}},{'arguments':{},'extra':'ambiguous'}):
            with self.assertRaises(OutputContractError):
                validated_output('planner',{'action':'search_documents',**extra},self.tools)
        with self.assertRaises(OutputContractError) as err:
            validated_output('planner',{'action':'not_a_registered_tool','arguments':{}},self.tools)
        self.assertNotIn('not_a_registered_tool',err.exception.code)

    def test_flat_only_with_complete_registered_tool_fields(self):
        context={'tools':[{'name':'search_documents','parameters':{'properties':{'entity':{},'query':{}},'required':['query']}}]}
        self.assertEqual(validated_output('planner',{'action':'search_documents','entity':'XYZ','query':'sales'},context)['arguments'],{'entity':'XYZ','query':'sales'})
        for extra in ({'entity':'XYZ'},{'query':'sales','extra':True}):
            with self.assertRaises(OutputContractError):validated_output('planner',{'action':'search_documents',**extra},context)
