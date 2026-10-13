"""Private JSON-over-stdin worker; credentials never appear in command arguments."""
import json
import sys

import requests


def perform(operation, payload):
    if operation == "fetch":
        from .fetcher import download, download_with_report
        if payload['options'].get('_stream_progress'):
            def progress(event):
                sys.stdout.buffer.write(json.dumps({'event': event}, ensure_ascii=False).encode('utf-8') + b'\n')
                sys.stdout.buffer.flush()
            payload['options']['_progress'] = progress
        operation = download_with_report if payload['options'].get('_download_report') else download
        return operation(payload["url"], payload["options"])
    if operation == "tcp":
        from .verifier import tcp_ready
        return tcp_ready(payload["node"], payload["timeout"])
    if operation == "probe":
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(**payload)
            if response.ok:
                data = response.json()
                return data.get("delay") if isinstance(data, dict) else None
            return None
    if operation == 'http-probe':
        import time
        started = time.monotonic()
        with requests.Session() as session:
            session.trust_env = False
            proxy = f"http://127.0.0.1:{payload['port']}"
            with session.get(payload['url'], proxies={'http': proxy, 'https': proxy},
                             timeout=payload['timeout'], stream=True) as response:
                return max(1, round((time.monotonic() - started) * 1000)) if 200 <= response.status_code < 400 else None
    raise ValueError("Unknown operation")


def respond(raw):
    try:
        message = json.loads(raw)
        result = {"ok": True, "result": perform(message["operation"], message["payload"])}
    except Exception as error:
        # No URLs, tokens, response bodies or exception details cross the error boundary.
        from .storage import AppError
        from .download_network import DownloadFailed, error_code
        result = {"ok": False, 'error_code': error.code if isinstance(error, DownloadFailed) else error_code(error)}
        if isinstance(error, AppError) and not isinstance(error, DownloadFailed):
            result['error_code'] = 'oversize' if str(error) == '订阅响应过大' else 'format'
        if isinstance(error, DownloadFailed): result['attempts'] = error.attempts
    return json.dumps(result, ensure_ascii=False).encode('utf-8')


def main():
    if '--persistent' in sys.argv:
        for line in sys.stdin.buffer:
            sys.stdout.buffer.write(respond(line) + b'\n'); sys.stdout.buffer.flush()
    else:
        sys.stdout.buffer.write(respond(sys.stdin.buffer.read()))


if __name__ == "__main__": main()
