def pytest_configure(config):
    # Keep the optional pytest-socket marker known when the plugin is absent.
    config.addinivalue_line(
        "markers", "allow_hosts(hosts): Restrict socket connections to the listed hosts"
    )
