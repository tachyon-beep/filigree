"""Dashboard API tests — split into domain modules.

See:
  - api/test_api.py            (core issue endpoints, workflow, batch, dashboard behavior)
  - api/test_files_api.py      (file records, scan results, scan source filtering)
  - api/test_graph_api.py      (graph serialization, frontend contracts, bounded int)
  - api/test_multi_project.py  (ProjectStore, server-mode routing, multi-project management)
  - api/conftest.py            (shared fixtures: dashboard_db, client, multi_client, project_store)
"""
