import threading
from unittest.mock import Mock

import pytest
import requests

from nodeharbor.exporter import publish
from nodeharbor.storage import AppError, Paths, read_json, write_json
from nodeharbor.subscription import create_server


def test_subscription_only_serves_export_allowlist(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    folder, _ = publish(paths, [], {})
    (folder / 'self.yaml').write_text('legacy-private-data')
    (folder / 'combined.yaml').write_text('legacy-private-data')
    with create_server(paths, 0) as server:
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            with requests.Session() as session:
                session.trust_env = False
                base = f"http://127.0.0.1:{server.server_port}"
                assert session.get(base + "/free.yaml", timeout=2).status_code == 200
                assert session.get(base + "/mihomo.yaml", timeout=2).status_code == 200
                assert session.get(base + "/config/secrets.json", timeout=2).status_code == 404
                assert session.get(base + "/report.json", timeout=2).status_code == 404
                assert session.get(base + "/", timeout=2).status_code == 404
                assert session.get(base + '/self.yaml', timeout=2).status_code == 404
                assert session.get(base + '/combined.yaml', timeout=2).status_code == 404
                assert session.get(base + '/free.base64.txt', timeout=2).status_code == 404
        finally: server.shutdown(); thread.join(timeout=2)




def test_empty_sources_leave_no_exports(tmp_path):
    from nodeharbor.fetcher import fetch_all
    from nodeharbor.config import DEFAULTS
    from copy import deepcopy
    settings = deepcopy(DEFAULTS); settings['fetch']['github_sources'] = []
    with pytest.raises(AppError): fetch_all(settings, lambda _: None)
