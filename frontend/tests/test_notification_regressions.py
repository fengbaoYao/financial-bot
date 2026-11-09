from unittest.mock import patch
from api.financial_bot import dispatch


def test_manual_digest_retry_dispatches_webhook():
    with patch.object(dispatch, '_webhook', return_value=True):
        assert dispatch.dispatch_channel({}, 'manual_digest_webhook') is True


def test_delivery_records_exclude_unconfigured_channels():
    with patch.dict('os.environ', {}, clear=True):
        assert dispatch.configured_results({'webhook': False, 'email': False}) == {}
        assert dispatch.configured_results({'webhook': False, 'email': True}, email_to='test@example.test') == {'email': True}


def test_webhook_rejects_private_destinations_and_redirects():
    from api.financial_bot.dispatch import _webhook
    with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]), patch('urllib.request.urlopen') as send:
        assert _webhook({}, 'https://example.test/hook') is False
        send.assert_not_called()
    assert _webhook({}, 'http://127.0.0.1/hook') is False


def test_recovery_is_partial_even_when_model_supplies_text():
    from api.financial_bot.executor import classify_response
    from api.schemas import AgentResponse
    response = AgentResponse(intent='pydantic_ai_agent', summary='模型知识补齐（未核验）', payload={'_meta': {'llm_research_recovery': True, 'unverified': True}})
    assert classify_response(response) == ('partial', 'unverified_recovery')


def test_webhook_connects_checked_ip_with_original_tls_hostname():
    from unittest.mock import MagicMock
    from api.financial_bot import dispatch
    raw = MagicMock()
    context = MagicMock()
    response = MagicMock(status=204)
    def send(conn, method, path, body, headers):
        conn.connect()
        assert method == 'POST' and path == '/hook?x=1'
    with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]) as resolve, \
         patch('socket.create_connection', return_value=raw) as connect, \
         patch('ssl.create_default_context', return_value=context), \
         patch('http.client.HTTPSConnection.request', send), \
         patch('http.client.HTTPSConnection.getresponse', return_value=response):
        assert dispatch._webhook({'title': 'test'}, 'https://example.test/hook?x=1') is True
        resolve.assert_called_once()
        connect.assert_called_once_with(('8.8.8.8', 443), 8)
        context.wrap_socket.assert_called_once_with(raw, server_hostname='example.test')


def test_webhook_rejects_mixed_dns_and_does_not_follow_redirect():
    from api.financial_bot import dispatch
    addresses = [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
    with patch('socket.getaddrinfo', return_value=addresses), patch('socket.create_connection') as connect:
        assert dispatch._webhook({}, 'https://example.test') is False
        connect.assert_not_called()
    with patch.object(dispatch, '_post_public_https', return_value=302) as post:
        assert dispatch._webhook({}, 'https://example.test') is False
        post.assert_called_once()


def test_research_limit_prevents_fresh_automatic_attempt():
    from api.financial_bot.executor import TaskExecutor
    from api.schemas import AgentResponse
    stopped = AgentResponse(intent='error', summary='limit', payload={'_meta': {'limit_reached': True, 'output_fallback': 'text'}})
    assert TaskExecutor._automatic_retry_permitted(stopped) is False
    normal = AgentResponse(intent='pydantic_ai_agent', summary='normal', payload={})
    assert TaskExecutor._automatic_retry_permitted(normal) is True


def test_webhook_real_tls_to_isolated_local_receiver(tmp_path):
    import http.server
    import socket
    import ssl
    import subprocess
    import threading
    received = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=example.test', '-addext', 'subjectAltName=DNS:example.test'], check=True, capture_output=True)
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert), str(key))
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_ctx = ssl.create_default_context(cafile=str(cert))
    def connect_local(_address, timeout):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(('127.0.0.1', server.server_port))
        return sock
    try:
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
             patch('socket.create_connection', side_effect=connect_local), \
             patch('ssl.create_default_context', return_value=client_ctx):
            assert dispatch._webhook({'title': 'isolated test'}, 'https://example.test/hook') is True
        assert len(received) == 1 and received[0][0] == '/hook'
        assert b'isolated test' in received[0][1]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_deep_research_flags_unverifiable_citations_and_causal_claims():
    from api.financial_bot.executor import assess_deep_evidence_quality, classify_response
    from api.schemas import AgentResponse
    result = AgentResponse(intent='pydantic_ai_agent', summary='结论：下跌主要与板块同步，并没有被个股事件主导。板块 beta 为主因 [4]（来源缺少可验证链接）。', payload={'_meta': {}}, data_gaps=[])
    assess_deep_evidence_quality(result, language='zh-CN')
    assert classify_response(result)[0] == 'partial'
    assert '因果' in result.summary[:100]
    assert any('链接' in gap for gap in result.data_gaps)
