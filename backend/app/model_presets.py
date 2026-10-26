import os
from .db import Provider


GATEWAY = 'https://newsai.woa.com/starcode-codex/v1'
DEEPSEEK_BASE = 'https://api.deepseek.com'
DEEPSEEK_PROVIDER_ID = 'deepseek-official-flash'


def install_model_presets(db):
    if os.getenv('MODEL_KEY_DEEPSEEK') and not db.get(Provider, DEEPSEEK_PROVIDER_ID):
        db.add(Provider(id=DEEPSEEK_PROVIDER_ID,name='DeepSeek 官方 · Flash',kind='model',
                        driver='openai_compatible',priority=5,enabled=True,
                        config={'base_url':DEEPSEEK_BASE,'model':'deepseek-flash','key_env':'MODEL_KEY_DEEPSEEK',
                                'api_format':'chat_completions','max_output_tokens':'6000','timeout_seconds':'90'}))
    if not os.getenv('MODEL_KEY_STARCODE'):
        return
    for index, model in enumerate(('gpt-6.1-sol', 'gpt-5.6-luna')):
        identifier = 'starcode-' + model
        if db.get(Provider, identifier):
            continue
        db.add(Provider(id=identifier,name=model,kind='model',driver='openai_compatible',priority=10+index,
                        enabled=True,config={'base_url':GATEWAY,'model':model,'key_env':'MODEL_KEY_STARCODE',
                                             'api_format':'responses','reasoning_effort':'high',
                                             'max_output_tokens':'6000','timeout_seconds':'90'}))
