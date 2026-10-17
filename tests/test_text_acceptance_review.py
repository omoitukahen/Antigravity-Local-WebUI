import json,uuid
from fastapi.testclient import TestClient
from agweb.config import Config,sha
from agweb.server import create_app
from agweb import gate


def test_review_inherits_only_matching_unchanged_live_receipt(tmp_path,monkeypatch):
    config=Config(tmp_path,'review-key',8835);config.data.mkdir()
    (config.data/'models.json').write_text(json.dumps({'models':['mock-model']}),encoding='utf-8')
    monkeypatch.setattr(gate,'status',lambda c:{'ready':True,'operational':True,'fingerprint':'current','models':['mock-model'],'allowed_pairs':[]})
    folder=config.data/'validation';folder.mkdir()
    receipt_id=str(uuid.uuid4());receipt_path=folder/(receipt_id+'.json')
    receipt={'passed':True,'fingerprint':'baseline','terminal':{'state':'completed','active':0,'exit_code':0},'guard_write_observed':True,'usage':{'total_tokens':30}}
    receipt_path.write_text(json.dumps(receipt),encoding='utf-8');original=receipt_path.read_bytes()
    review_id=str(uuid.uuid4());review_path=folder/(review_id+'.json')
    review={'purpose':'text_output_configuration_review','passed':True,'fingerprint':'current','baseline_fingerprint':'baseline','receipt_id':receipt_id,'receipt_sha256':sha(receipt_path),'approval_reference':'User approved offline-only UI changes','additional_model_sends':0}
    proof={'passed':True,'fingerprint':'current','receipt_id':receipt_id,'receipt_sha256':sha(receipt_path),'review_id':review_id}
    def save_review(value):
        review_path.write_text(json.dumps(value),encoding='utf-8');proof['review_sha256']=sha(review_path)
        (config.data/'text-output-acceptance.json').write_text(json.dumps(proof),encoding='utf-8')
    save_review(review)
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer review-key'}) as client:
        ready=lambda:client.get('/api/status').json()['text_output_ready']
        assert ready() and receipt_path.read_bytes()==original
        for change in ({'purpose':'other'},{'fingerprint':'stale'},{'baseline_fingerprint':'wrong'},{'receipt_id':str(uuid.uuid4())},{'receipt_sha256':'wrong'},{'passed':False},{'additional_model_sends':1},{'approval_reference':''}):
            save_review({**review,**change});assert not ready(),change
        save_review(review);review_path.write_text('{}',encoding='utf-8');assert not ready()
        save_review(review);receipt_path.write_text('{}',encoding='utf-8');assert not ready()
