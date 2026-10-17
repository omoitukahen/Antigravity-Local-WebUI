import json
import pytest
from agweb.config import Config
from agweb import gate
from agweb.validation import authorize,ledger,save,finalize,reviewed_effort
from agweb.diagnostics import parse_models
from test_supervisor import Probe

def test_model_list_actual_tab_format():
    assert parse_models('gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\nerror authentication required')==['gemini-3.8-flash-low']
    assert parse_models('no model available')==[]

def test_variant_effort_evidence_cannot_be_reused_for_another_model_or_cli():
    review={'effort':'medium','effort_evidence':{'method':'reviewed_model_variant','model':'verified-model',
        'cli_sha256':'reviewed-binary','approval_reference':'Human approved variant evidence','logs':[{'sha256':'observed-log'}]}}
    assert reviewed_effort(review,'verified-model','reviewed-binary')=='medium'
    assert reviewed_effort(review,'different-model','reviewed-binary') is None
    assert reviewed_effort(review,None,'reviewed-binary') is None
    assert reviewed_effort(review,'verified-model','updated-binary') is None

def test_budget_survives_restart_and_cannot_expand_initial_pilot(tmp_path):
    c=Config(tmp_path,'test',0)
    with pytest.raises(ValueError):authorize(c,'exact-model','low',['web'],1,30,'not a pilot')
    id=authorize(c,'exact-model','low',['normal'],1,30,'User explicitly approved one pilot')
    value=ledger(c);value['approvals'][0]['used']=1;value['approvals'][0]['remaining']=0;value['total_launches']=1
    value['stop']='usage unknown';save(c.data/'validation-run-ledger.json',value)
    assert ledger(Config(tmp_path,'new-start',0))['approvals'][0]['remaining']==0
    with pytest.raises(ValueError):authorize(c,'exact-model','low',['web'],1,30,'additional')
    with pytest.raises(ValueError):finalize(c)

@pytest.mark.parametrize('scenario',['file_denied','delegate_denied','unknown_denied','mcp_denied','url_denied','url_credentials','bad_arguments','chunk_denied'])
def test_policy_through_actual_hook_program(tmp_path,scenario):
    p=Probe(tmp_path,scenario)
    t=p.collect()
    assert t['state']=='failed' and t['reason']=='guard_denied' and t['active']==0

def test_receipt_gate_invalidates_configuration_changes(tmp_path,monkeypatch):
    c=Config(tmp_path,'test',0);c.data.mkdir()
    monkeypatch.setattr(gate,'snapshot',lambda c:{'guard':'v1'})
    signature=gate.fingerprint({'guard':'v1'})
    save(c.data/'configuration-review.json',{'confirmed':True})
    from agweb.config import sha
    receipts={}
    for index,case in enumerate(gate.REQUIRED_CASES-{'backend_cleanup'},1):
        id='00000000-0000-0000-0000-'+str(index).zfill(12)
        save(c.data/'validation'/(id+'.json'),{'fingerprint':signature,'passed':True,'terminal':{'active':0},'model':'exact-model','effort':'low','case':case,
            'events':[{'kind':'guard','stage':'tool','tool':t} for t in gate.REQUIRED_TOOLS]})
        receipts[id]=sha(c.data/'validation'/(id+'.json'))
    proof={'fingerprint':signature,'passed_cases':list(gate.REQUIRED_CASES),'models':['exact-model'],'tested_pairs':[{'model':'exact-model','effort':'low'}],
        'customizations_verified':True,'receipts':receipts,'review_sha256':sha(c.data/'configuration-review.json')}
    save(c.data/'live-acceptance.json',proof)
    assert gate.status(c)['ready'] is True
    monkeypatch.setattr(gate,'snapshot',lambda c:{'guard':'v2'})
    assert gate.status(c)['ready'] is False

@pytest.fixture
def operation_setup(tmp_path,monkeypatch):
    from agweb.config import sha
    c=Config(tmp_path,'test',0);c.data.mkdir()
    current={'cli_sha256':'same-cli','guard':'unchanged'}
    monkeypatch.setattr(gate,'snapshot',lambda c:current)
    signature=gate.fingerprint(current)
    save(c.data/'configuration-review.json',{'fingerprint':signature,'executable_customizations':[],
        'loading_sources_verified':True,'auth_method':'reviewed auth','endpoint':'reviewed endpoint'})
    receipts={}
    for index,case in enumerate(('normal','resume'),1):
        id='00000000-0000-0000-0000-'+str(index).zfill(12)
        save(c.data/'validation'/(id+'.json'),{'fingerprint':'baseline','passed':True,'case':case,
            'actual_model':'exact-model','actual_effort':'medium','terminal':{'state':'completed','active':0,'exit_code':0}})
        receipts[id]=sha(c.data/'validation'/(id+'.json'))
    path=c.data/'operation-approval.json'
    proof={'fingerprint':signature,'baseline_fingerprint':'baseline','cli_sha256':'same-cli',
        'approval_reference':'Human chooses operation over further live tests',
        'review_sha256':sha(c.data/'configuration-review.json'),'receipts':receipts,
        'tested_pairs':[{'model':'exact-model','effort':'medium'}],'unverified_cases':['web']}
    save(path,proof)
    save(c.data/'models.json',{'fingerprint':signature,'models':['exact-model']})
    return c,current,proof,path,signature

def test_explicit_operation_mode_keeps_evidence_and_configuration_checks(operation_setup):
    c,current,proof,path,signature=operation_setup
    result=gate.status(c)
    assert result['ready'] and result['operational'] and result['unverified_cases']==['web']
    assert not (c.data/'live-acceptance.json').exists()
    current['cli_sha256']='updated-cli'
    result=gate.status(c)
    assert not result['ready'] and result['block_code']=='cli_changed'
    assert 'CLI' in result['reason'] and '更新' in result['reason']
    current['cli_sha256']='same-cli'
    current['guard']='changed'
    result=gate.status(c)
    assert not result['ready'] and result['block_code']=='configuration_changed'
    current['guard']='unchanged'
    proof['cli_update_without_live_test']=True;save(path,proof)
    result=gate.status(c)
    assert result['ready'] and result['cli_update_without_live_test']
    assert '未確認' in result['reason']
    save(c.data/'models.json',{'fingerprint':signature,'models':['exact-model','another-model']})
    proof['operating_pairs']=proof['tested_pairs']+[{'model':'another-model','effort':'high'}]
    proof['model_selection_reference']='Human requests model selection during operation';save(path,proof)
    result=gate.status(c)
    assert result['ready'] and result['allowed_pairs']==proof['operating_pairs']
    assert result['tested_pairs']==[{'model':'exact-model','effort':'medium'}]
    assert result['models']==['another-model','exact-model']
    proof['model_selection_reference']='';save(path,proof)
    assert not gate.status(c)['ready']
    proof['model_selection_reference']='Human requests model selection during operation'
    proof['approval_reference']='';save(path,proof)
    assert not gate.status(c)['ready']

@pytest.mark.parametrize('extended',[False,True])
def test_removed_models_only_disable_the_missing_approved_pairs(operation_setup,extended):
    c,current,proof,path,signature=operation_setup
    if extended:
        proof['operating_pairs']=proof['tested_pairs']+[{'model':'another-model','effort':'high'}]
        proof['model_selection_reference']='Human approved model selection'
        save(path,proof)
    available=['another-model','new-unapproved-model'] if extended else ['exact-model','new-unapproved-model']
    save(c.data/'models.json',{'fingerprint':signature,'models':available})
    before=path.read_bytes()
    result=gate.status(c)
    expected=[{'model':'another-model','effort':'high'}] if extended else proof['tested_pairs']
    assert result['ready'] and result['allowed_pairs']==expected
    assert result['tested_pairs']==proof['tested_pairs']
    assert result['models']==[expected[0]['model']]
    assert path.read_bytes()==before
    save(c.data/'models.json',{'fingerprint':signature,'models':['new-unapproved-model']})
    result=gate.status(c)
    assert not result['ready'] and result['block_code']=='no_operating_models'
    assert result['models']==[] and result['allowed_pairs']==[]

def test_model_filter_keeps_source_and_approval_validation(operation_setup):
    c,current,proof,path,signature=operation_setup
    save(c.data/'models.json',{'fingerprint':'different-configuration','models':['exact-model']})
    assert not gate.status(c)['ready']
    save(c.data/'models.json',{'fingerprint':signature,'models':['exact-model']})
    proof['operating_pairs']=[{'model':'removed-model','effort':'invalid'}]
    proof['model_selection_reference']='Human approved model selection';save(path,proof)
    assert not gate.status(c)['ready']
