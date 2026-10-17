import uuid
from pathlib import Path
import pytest
from agweb.store import Store, Conflict
from agweb.output import registered_folder, allocate_output, OutputError
from agweb.output import finish_output


def test_settings_revision_persistence_and_reservation_snapshot(tmp_path):
    folder=tmp_path/'novels';folder.mkdir()
    other=tmp_path/'other';other.mkdir()
    store=Store(tmp_path/'data')
    assert store.output_settings()=={'folder':'','revision':0}
    settings=store.update_output_settings(str(folder),0)
    assert settings=={'folder':str(folder),'revision':1}
    with pytest.raises(Conflict):store.update_output_settings(str(other),0)
    c=store.create('mock-model','low',output_mode='text_file')
    run,_=store.reserve(c['id'],{'request_id':str(uuid.uuid4()),'revision':0,'text':'Novel.txt','timeout':30})
    assert run['output_root']==str(folder) and run['output']['name'] is None
    with pytest.raises(Conflict):store.update_output_settings(str(other),1)
    store.event(run['id'],{'kind':'output_assigned','name':'Novel.txt'})
    store.event(run['id'],{'kind':'guard','tool':'write_to_file'})
    (folder/'Novel.txt').write_text('本文\n',encoding='utf-8',newline='')
    store.close();store=Store(tmp_path/'data');store.recover()
    assert store.run(run['id'])['output']['state']=='incomplete'
    assert store.output_bytes(run['id'])[0]=='本文\n'.encode()
    store.update_output_settings(str(other),1)
    assert store.output_path(store.run(run['id']))==folder/'Novel.txt'
    store.delete_conversation(c['id'])
    assert (folder/'Novel.txt').read_text(encoding='utf-8')=='本文\n'
    store.close()


def test_unregistered_blocks_only_file_reservation(tmp_path):
    store=Store(tmp_path/'data')
    for mode in ('text_file','chat'):
        c=store.create('mock-model','low',output_mode=mode)
        body={'request_id':str(uuid.uuid4()),'revision':0,'text':'x','timeout':30}
        if mode=='text_file':
            with pytest.raises(ValueError):store.reserve(c['id'],body)
        else:assert store.reserve(c['id'],body)[0]['output'] is None
    store.close()


def test_folder_and_file_name_checks_and_numbering(tmp_path):
    assert registered_folder(str(tmp_path))==tmp_path
    for invalid in ('relative',str(tmp_path/'missing'),r'\\server\share'):
        with pytest.raises(OutputError):registered_folder(invalid)
    for name in ('CON.txt','NUL.txt','aux.txt','bad?.txt','bad:.txt','bad.txt.','a.md','../outside.txt','dir/file.txt'):
        with pytest.raises(OutputError):allocate_output(str(tmp_path/name),tmp_path)
    first=tmp_path/'日本語.txt';first.write_text('existing',encoding='utf-8')
    (tmp_path/'日本語 (2).txt').write_text('other',encoding='utf-8')
    assert allocate_output(str(first),tmp_path)==tmp_path/'日本語 (3).txt'


def test_registered_parent_junction_is_rejected(tmp_path):
    import subprocess
    real=tmp_path/'real';real.mkdir();(real/'nested').mkdir()
    link=tmp_path/'link'
    result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(real)],capture_output=True)
    assert result.returncode==0
    try:
        with pytest.raises(OutputError):registered_folder(str(link/'nested'))
    finally:link.rmdir()


def test_old_output_remains_readable_after_repeatable_migration(tmp_path):
    import json
    store=Store(tmp_path/'data');c=store.create('mock-model','low')
    run,_=store.reserve(c['id'],{'request_id':str(uuid.uuid4()),'revision':0,'text':'old','timeout':30})
    path=store.output_path(run);path.parent.mkdir(parents=True);path.write_bytes(b'legacy\n')
    metadata=finish_output(path,store.data,True,True)
    with store.db:
        store.db.execute("UPDATE runs SET state='completed',output=? WHERE id=?",(json.dumps(metadata),run['id']))
        store.db.execute('ALTER TABLE runs DROP COLUMN output_root')
        store.db.execute('DROP TABLE output_settings')
    store.close()
    for _ in range(2):
        store=Store(tmp_path/'data')
        assert store.output_settings()=={'folder':'','revision':0}
        assert store.run(run['id'])['output_root'] is None
        assert store.output_bytes(run['id'])[0]==b'legacy\n'
        store.close()
