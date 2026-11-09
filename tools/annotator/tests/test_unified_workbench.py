from tools.annotator.unified_workbench import create_unified_server


def test_unified_workbench_serves_both_workflows_with_accessible_tabs():
    server = create_unified_server(
        "http://127.0.0.1:7412/", "http://127.0.0.1:7411/", port=0
    )
    try:
        import threading
        from urllib.request import urlopen

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "场景设计标注" in page
            assert "运行证据标注" in page
            assert 'role="tablist"' in page
            assert 'src="http://127.0.0.1:7412/"' in page
            assert 'data-src="http://127.0.0.1:7411/"' in page
            import re
            without_data_src = re.sub(r'data-src="[^"]+"', "", page)
            assert 'src="http://127.0.0.1:7411/"' not in without_data_src
    finally:
        server.shutdown()
        server.server_close()
