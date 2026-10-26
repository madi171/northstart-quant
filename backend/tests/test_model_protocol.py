import json

import httpx
import pytest

from app.providers import OpenAICompatibleModel, validate_model_config


BASE='https://models.example/v1'
SECRET='test-only-do-not-leak'
PROPOSAL={'code':'def target(history, params):\n return 0.3','params':{},'reason':'fixture',
          'name':'定权持有 · 三成仓位','strategy_logic':'维持三成目标仓位。',
          'change_summary':'从空仓改为三成目标仓位。','naming_reason':'体现定权与仓位逻辑。'}


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv('MODEL_KEY_TEST',SECRET)
    monkeypatch.setenv('MODEL_ALLOWED_BASE_URLS',BASE)
    return {'base_url':BASE,'model':'fixture','key_env':'MODEL_KEY_TEST'}


def mock_client(monkeypatch, handler):
    from app import providers
    client=httpx.Client
    def factory(*args,**kwargs):
        assert kwargs['follow_redirects'] is False
        return client(*args,transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(providers.httpx,'Client',factory)


@pytest.mark.parametrize('api_format',['chat_completions','responses'])
def test_model_protocol_requests_and_safe_usage(monkeypatch,config,api_format):
    captured=[]
    config.update(api_format=api_format,reasoning_effort='high',max_output_tokens='6000',timeout_seconds='90')
    def handler(request):
        captured.append(request)
        body=json.loads(request.content)
        assert request.headers['authorization']=='Bearer '+SECRET
        assert SECRET not in str(body)
        assert 'temperature' not in body
        if api_format=='responses':
            assert request.url.path=='/v1/responses'
            assert body['max_output_tokens']==6000 and body['reasoning']=={'effort':'high'}
            assert body['input'][1]['content']=='{"test": "synthetic"}'
            response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'```json\n'+json.dumps(PROPOSAL)+'\n```'}]}],
                      'usage':{'input_tokens':123,'output_tokens':70,'total_tokens':193,'unsafe':SECRET}}
        else:
            assert request.url.path=='/v1/chat/completions'
            assert body['max_tokens']==6000 and body['reasoning_effort']=='high'
            assert body['response_format']=={'type':'json_object'}
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(PROPOSAL)}}],
                      'usage':{'prompt_tokens':123,'completion_tokens':70,'total_tokens':193,'unsafe':SECRET}}
        return httpx.Response(200,json=response)
    mock_client(monkeypatch,handler)
    result=OpenAICompatibleModel(config).propose({'test':'synthetic'})
    assert {key:result[key] for key in PROPOSAL}==PROPOSAL
    assert result['usage']['total_tokens']==193 and 'unsafe' not in result['usage']
    assert len(captured)==1


@pytest.mark.parametrize('base',['https://api.deepseek.com','https://models.example/v1'])
def test_deepseek_chat_uses_budgeted_json_and_non_thinking_only_on_official_host(monkeypatch,config,base):
    monkeypatch.setenv('MODEL_ALLOWED_BASE_URLS',base)
    config.update(base_url=base,model='deepseek-flash',api_format='chat_completions',max_output_tokens='6000')
    captured=[]
    def handler(request):
        captured.append(request)
        body=json.loads(request.content)
        assert request.url.path.endswith('/chat/completions')
        assert body['model']=='deepseek-flash'
        assert body['max_tokens']==6000
        assert body['response_format']=={'type':'json_object'}
        assert body['temperature']==.2
        assert 'reasoning_effort' not in body
        assert any('JSON' in message['content'] for message in body['messages'])
        if base=='https://api.deepseek.com':
            assert body['thinking']=={'type':'disabled'}
        else:
            assert 'thinking' not in body
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(PROPOSAL)}}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    mock_client(monkeypatch,handler)
    result=OpenAICompatibleModel(config).propose({'instruction':'synthetic test'})
    assert result['code']==PROPOSAL['code'] and result['usage']['total_tokens']==150
    assert len(captured)==1


@pytest.mark.parametrize('status',[301,401,403,429,500])
def test_model_http_errors_are_redacted_and_not_retried(monkeypatch,config,status):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,json={'error':SECRET},headers={'location':'https://untrusted.example'})
    mock_client(monkeypatch,handler)
    with pytest.raises(ValueError,match=f'HTTP {status}') as error:
        OpenAICompatibleModel(config).propose({})
    assert SECRET not in str(error.value)
    assert len(calls)==1


def test_model_timeout_is_redacted(monkeypatch,config):
    def handler(request):
        raise httpx.ReadTimeout(SECRET,request=request)
    mock_client(monkeypatch,handler)
    with pytest.raises(ValueError,match='超时') as error:
        OpenAICompatibleModel(config).propose({})
    assert SECRET not in str(error.value)


@pytest.mark.parametrize('response',[
    {'output_text':json.dumps(PROPOSAL),'status':'incomplete'},
    {'output_text':json.dumps({**PROPOSAL,'reason':SECRET}),'status':'completed'},
    {'output_text':'not json','status':'completed'},
    {'output_text':json.dumps({'code':1,'params':{}}),'status':'completed'},
    {'output_text':json.dumps({**PROPOSAL,'name':['invalid']}),'status':'completed'},
    {'output_text':json.dumps({**PROPOSAL,'name':'x'*81}),'status':'completed'},
    {'output_text':json.dumps({**PROPOSAL,'strategy_logic':{'invalid':1}}),'status':'completed'},
    {'output_text':json.dumps({**PROPOSAL,'naming_reason':SECRET}),'status':'completed'},
])
def test_incomplete_or_secret_echo_responses_rejected(monkeypatch,config,response):
    config['api_format']='responses'
    mock_client(monkeypatch,lambda request:httpx.Response(200,json=response))
    with pytest.raises(ValueError,match='格式无效|不完整') as error:
        OpenAICompatibleModel(config).propose({})
    assert SECRET not in str(error.value)


def test_response_size_is_bounded(monkeypatch,config):
    mock_client(monkeypatch,lambda request:httpx.Response(200,content=b'x'*200001))
    with pytest.raises(ValueError,match='过大'):
        OpenAICompatibleModel(config).propose({})


def test_preflight_never_calls_network(monkeypatch,config):
    mock_client(monkeypatch,lambda request:pytest.fail('preflight must be offline'))
    assert validate_model_config(config) is None
    with pytest.raises(ValueError,match='HTTPS'):
        validate_model_config({**config,'base_url':'https://other.example/v1'})
    with pytest.raises(ValueError,match='HTTPS'):
        validate_model_config({**config,'base_url':'http://models.example/v1'})
    with pytest.raises(ValueError,match='凭据'):
        validate_model_config({**config,'key_env':'RAW_KEY'})
    monkeypatch.delenv('MODEL_KEY_TEST')
    with pytest.raises(ValueError,match='密钥未配置'):
        validate_model_config(config)
