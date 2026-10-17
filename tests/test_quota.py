import pytest
import json
from pathlib import Path
from agweb.quota import UsageScreen


PANEL_TEXT = (Path(__file__).parent/'fixtures/quota-1.3.0.txt').read_text(encoding='utf-8').rstrip()
PANEL = PANEL_TEXT.replace('\n','\r\n')+'\r\n↑/↓ Scroll · esc Close'
ARGON_PANEL_TEXT = (Path(__file__).parent/'fixtures/quota-argon-1.3.0.txt').read_text(encoding='utf-8').rstrip()
ARGON_PANEL = ARGON_PANEL_TEXT.replace('\n','\r\n')+'\r\n↑/↓ Scroll · esc Close'
QUOTA_TEXT = '''Models & Quota

GEMINI MODELS
  Weekly Limit Remaining
    [██████████████████████████████████████████████████] 99.94%
    Refreshes in 80h 42m
  Five Hour Limit Remaining
    [██████████████████████████████████████████████████] 100.00%
    Quota available

CLAUDE AND GPT MODELS
  Weekly Limit Remaining
    [███████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░] 46.39%
    Refreshes in 163h 30m
  Five Hour Limit Remaining
    [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 0.00%
    Refreshes in 30m'''


def test_screen_repaint_and_private_banner():
    s=UsageScreen()
    s.feed(b'person@example.com\r\nAntigravity CLI 1.3.0\r\n> \r\n? for shortcuts')
    assert s.ready()
    s.feed(b'\x1b[2J\x1b[H'+PANEL.encode())
    assert s.panel() == QUOTA_TEXT
    assert '@' not in s.panel()


def test_loading_unknown_and_unicode_split():
    s=UsageScreen()
    s.feed('└ Models & Quota\r\nLoading...'.encode())
    assert s.panel() is None
    text=('\x1b[2J\x1b[H'+PANEL).encode()
    for b in text:s.feed(bytes([b]))
    assert s.panel()==QUOTA_TEXT
    s.feed(b'\x1b[2J\x1b[Hunexpected output')
    assert s.panel() is None


def test_output_limit():
    s=UsageScreen(limit=20)
    with pytest.raises(ValueError):s.feed(b'x'*21)


def test_trust_prompt_is_not_confirmed():
    s=UsageScreen()
    s.feed(b'Do you trust the contents of this project?\r\n> Yes, I trust this folder')
    assert s.blocked_reason()
    assert not s.ready()


@pytest.mark.parametrize('unrelated',['person@example.com','Bearer secret','token usage information','https://example.com'])
def test_panel_omits_unrelated_account_and_explanation(unrelated):
    s=UsageScreen()
    s.feed(PANEL.replace('GEMINI MODELS',unrelated+'\r\nGEMINI MODELS').encode())
    assert s.panel()==QUOTA_TEXT
    assert unrelated not in s.panel()


def test_private_content_inside_quota_value_is_rejected():
    s=UsageScreen()
    s.feed(PANEL.replace('99.94%','99.94% person@example.com').encode())
    assert s.panel() is None


def test_partial_quota_panel_is_not_a_complete_result():
    s=UsageScreen()
    s.feed((PANEL.split('CLAUDE AND GPT MODELS')[0]+'esc Close').encode())
    assert s.panel() is None


def test_replay_captured_cli_screen():
    s=UsageScreen()
    s.feed(('Antigravity CLI 1.3.0\r\n>\r\n'+PANEL).encode())
    assert s.panel()==QUOTA_TEXT
    assert all(value in s.panel() for value in ('99.94%','100.00%','46.39%','0.00%','Refreshes in 30m'))


def test_argon_weekly_quota_from_sanitized_user_screen():
    s=UsageScreen()
    s.feed(ARGON_PANEL.encode())
    text=s.panel()
    assert text and text.endswith('''GEMINI 4 ARGON
  Gemini 4 Argon Weekly Limit
    [█████████████████████████████████████████████████░] 98.38%
    Refreshes in 167h 25m''')
    assert text.index('GEMINI MODELS')<text.index('CLAUDE AND GPT MODELS')<text.index('GEMINI 4 ARGON')
    assert all(value in text for value in ('99.99%','99.97%','100.00%'))
    assert text.count('Five Hour Limit Remaining')==2
    assert 'For paid Pro plans' not in text and 'Models within this group' not in text


@pytest.mark.parametrize('unrelated',['person@example.com','Bearer secret','token usage information','https://example.com'])
def test_argon_excludes_private_banner_and_explanations(unrelated):
    s=UsageScreen()
    s.feed(ARGON_PANEL.replace('GEMINI 4 ARGON','GEMINI 4 ARGON\r\n'+unrelated).encode())
    text=s.panel()
    assert text and '98.38%' in text and unrelated not in text


@pytest.mark.parametrize('old,new',[
    ('98.38%','101.00%'),
    ('98.38%','-1.00%'),
    ('98.38%','98.38% person@example.com'),
    ('Refreshes in 167h 25m',''),
    ('Refreshes in 167h 25m','Refreshes in unknown'),
    ('GEMINI 4 ARGON','GEMINI 4 ARGON\r\nGEMINI 4 ARGON'),
    ('Gemini 4 Argon Weekly Limit','Gemini 4 Argon Weekly Limit\r\nGemini 4 Argon Weekly Limit'),
])
def test_argon_invalid_or_duplicate_values_do_not_return_partial_success(old,new):
    s=UsageScreen()
    s.feed(ARGON_PANEL.replace(old,new).encode())
    assert s.panel() is None


def test_argon_incomplete_section_waits_for_the_rest_of_the_panel():
    s=UsageScreen()
    s.feed((ARGON_PANEL.split('Gemini 4 Argon Weekly Limit')[0]+'esc Close').encode())
    assert s.panel() is None
    # Replay a completed redraw with arbitrarily split UTF-8, including the bar.
    for b in ('\x1b[2J\x1b[H'+ARGON_PANEL).encode():s.feed(bytes([b]))
    assert '98.38%' in s.panel()


@pytest.mark.parametrize('percentage,reset',[('0.00%','Refreshes in 1h'),('100.00%','Quota available')])
def test_argon_zero_and_full_quota(percentage,reset):
    s=UsageScreen()
    s.feed(ARGON_PANEL.replace('98.38%',percentage).replace('Refreshes in 167h 25m',reset).encode())
    assert 'GEMINI 4 ARGON' in s.panel()
    assert s.panel().endswith(percentage+'\n    '+reset)


def test_unverified_real_adapter_does_not_spawn(tmp_path,monkeypatch):
    from agweb.config import Config
    from agweb.quota import collect_quota
    def forbidden(*args,**kwargs):raise AssertionError('must not launch')
    monkeypatch.setattr('agweb.quota.subprocess.Popen',forbidden)
    result=collect_quota(Config(tmp_path,'',0))
    assert not result['ok'] and '接続確認待ち' in result['reason']


@pytest.mark.parametrize('as_list',[False,True])
def test_real_quota_uses_existing_trusted_profile(tmp_path,monkeypatch,as_list):
    from agweb.config import Config
    from agweb.quota import quota_workdir
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    settings=tmp_path/'.gemini/antigravity-cli/settings.json'
    settings.parent.mkdir(parents=True)
    trusted=[str(tmp_path)] if as_list else str(tmp_path)
    original=json.dumps({'trustedWorkspaces':trusted})
    settings.write_text(original,encoding='utf-8')
    assert quota_workdir(Config(tmp_path/'app','',0))==tmp_path
    assert settings.read_text(encoding='utf-8')==original
    assert not (tmp_path/'app/data/diagnostics/quota').exists()


@pytest.mark.parametrize('trusted',[None,'different-folder',[],['different-folder'],[None],{}])
def test_quota_does_not_implicitly_trust_a_directory(tmp_path,monkeypatch,trusted):
    from agweb.config import Config
    from agweb.quota import quota_workdir
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    settings=tmp_path/'.gemini/antigravity-cli/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'trustedWorkspaces':trusted}),encoding='utf-8')
    with pytest.raises(ValueError,match='信頼済み'):
        quota_workdir(Config(tmp_path/'app','',0))


@pytest.mark.parametrize('change',['relocation','added','changed','removed','cli','environment'])
def test_quota_configuration_after_relocation(tmp_path,monkeypatch,change):
    from agweb.config import Config
    from agweb.quota import verified_configuration
    before={'cli_sha256':'cli','files':{'settings.json':'settings','old/AGENTS.md':None},
        'overrides':{},'cli_environment':{'AGY_CLI_DISABLE_AUTO_UPDATE':'true'}}
    current={**before,'files':{'settings.json':'settings','new/AGENTS.md':None}}
    if change=='added':current['files']['new/AGENTS.md']='new-rule'
    if change=='changed':current['files']['settings.json']='different'
    if change=='removed':del current['files']['settings.json']
    if change=='cli':current['cli_sha256']='updated-cli'
    if change=='environment':current['overrides']={'GEMINI_API_ENDPOINT':'different'}
    c=Config(tmp_path,'',0)
    (c.data/'validation').mkdir(parents=True)
    (c.data/'validation/quota-buttons-baseline.json').write_text(
        json.dumps({'snapshot':before}),encoding='utf-8')
    (c.data/'configuration-review.json').write_text(json.dumps({
        'loading_sources_verified':True,'executable_customizations':[]}),encoding='utf-8')
    monkeypatch.setattr('agweb.quota.gate.snapshot',lambda config:current)
    assert verified_configuration(c)==(change=='relocation')
