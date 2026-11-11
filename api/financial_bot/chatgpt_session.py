"""Separate local Sign in with ChatGPT registration for Financial Bot.

Uses the pinned TradingAgents OAuth implementation but a distinct application
name and credential file. Tokens never leave the local server or enter the UI.
"""
from functools import lru_cache
import importlib.util
from pathlib import Path

SESSION_DIR = Path.home() / '.financial-bot'


@lru_cache(maxsize=4)
def _load(path: str):
    spec = importlib.util.find_spec('tradingagents.llm_clients.chatgpt_auth')
    if spec is None or not spec.origin:
        raise RuntimeError('ChatGPT sign-in support is unavailable')
    independent = importlib.util.spec_from_file_location('financial_bot_chatgpt_oauth', spec.origin)
    if independent is None or independent.loader is None:
        raise RuntimeError('ChatGPT sign-in support is unavailable')
    module = importlib.util.module_from_spec(independent)
    independent.loader.exec_module(module)
    module.APP_NAME = 'Financial Bot'
    module.AUTH_FILE = Path(path) / 'chatgpt-auth.json'
    return module


def load_auth_module():
    return _load(str(SESSION_DIR))


def is_signed_in() -> bool:
    try:
        return bool(load_auth_module().signed_in_as())
    except Exception:
        return False


def sign_in():
    import webbrowser
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    SESSION_DIR.chmod(0o700)
    def notify(line):
        if line.startswith('https://auth.openai.com/'):
            webbrowser.open(line)
        else:
            print(line, flush=True)
    load_auth_module().sign_in(say=notify)
    print('ChatGPT sign-in completed.', flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['login']:
        raise SystemExit('Usage: python -m api.financial_bot.chatgpt_session login')
    sign_in()
