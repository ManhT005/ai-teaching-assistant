from fastapi.testclient import TestClient

from app.main import app


def test_demo_admin_serves_same_origin_shell_and_assets():
    with TestClient(app) as client:
        page = client.get("/demo/admin")
        assert page.status_code == 200
        assert "Trang quản trị demo" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "connect-src 'self'" in page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert client.get("/demo/admin/style.css").headers["content-type"].startswith("text/css")
        script = client.get("/demo/admin/console.js")
        assert script.status_code == 200
        assert script.headers["content-type"].startswith("text/javascript")
        assert client.get("/demo/admin/.env").status_code == 404
