from app.redact import redact


def test_kv_secret_and_jwt():
    s, n = redact("STRIPE_API_KEY=sk_live_51Hx token: eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4")
    assert "sk_live" not in s and "eyJ" not in s and n >= 2


def test_ip_email_urlcreds():
    s, n = redact("connect postgres://admin:hunter2@10.0.0.5:5432 by ops@acme.io")
    assert "hunter2" not in s and "10.0.0.5" not in s and "ops@acme.io" not in s
    assert n == 3


def test_plain_text_untouched():
    s, n = redact("KeyError: 'DATABASE_URL'")
    assert n == 0 and s == "KeyError: 'DATABASE_URL'"
