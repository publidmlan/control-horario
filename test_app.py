import os
import tempfile
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

TEST_DB = Path(tempfile.gettempdir()) / "horario_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = "sqlite:///" + str(TEST_DB)
os.environ["HORARIO_BACKUP_DIR"] = str(Path(tempfile.gettempdir()) / "horario_backups_test")

import main as app_main
from fastapi.testclient import TestClient

app_main.init_db()

client = TestClient(app_main.app)
anon = TestClient(app_main.app)


@pytest.fixture(autouse=True)
def clean_db():
    yield
    with app_main.engine.connect() as conn:
        conn.execute(text("DELETE FROM entries"))
        conn.execute(text("DELETE FROM settings"))
        conn.execute(text("DELETE FROM user_settings"))
        conn.execute(text("DELETE FROM sessions"))
        conn.execute(text("DELETE FROM invites"))
        conn.commit()
    if app_main.BACKUP_DIR.exists():
        for f in app_main.BACKUP_DIR.iterdir():
            if f.is_file():
                f.unlink()


def auth_headers(username="admin", pin="1234"):
    r = client.post("/api/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"Cookie": "auth_token=" + r.json()["token"]}


# ------------------------------------------------------------------
# Login / autenticacion
# ------------------------------------------------------------------
def test_login_incorrect_pin():
    r = client.post("/api/login", json={"username": "admin", "pin": "9999"})
    assert r.status_code == 401


def test_login_unknown_user():
    assert client.post("/api/login", json={"username": "nadie", "pin": "1234"}).status_code == 401


def test_login_correct_pin():
    r = client.post("/api/login", json={"username": "admin", "pin": "1234"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["token"]
    assert "auth_token" in r.cookies


def test_protected_endpoint_without_auth():
    r = anon.get("/api/week")
    assert r.status_code == 401


def test_logout():
    client.post("/api/login", json={"username": "admin", "pin": "1234"})
    r = client.get("/api/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_change_pin():
    h = auth_headers()
    assert client.post("/api/change-pin", json={"pin": "5678"}, headers=h).status_code == 200
    assert client.post("/api/login", json={"username": "admin", "pin": "5678"}).status_code == 200
    assert client.post("/api/login", json={"username": "admin", "pin": "1234"}).status_code == 401
    h2 = auth_headers("admin", "5678")
    assert client.post("/api/change-pin", json={"pin": "1234"}, headers=h2).status_code == 200
    assert client.post("/api/login", json={"username": "admin", "pin": "1234"}).status_code == 200


# ------------------------------------------------------------------
# Multi-usuario: usuarios e invitaciones
# ------------------------------------------------------------------
def _create_user(username, pin="2222", via_invite=False):
    if via_invite:
        inv = client.post("/api/invites", headers=auth_headers()).json()
        assert client.post("/api/register", json={"code": inv["code"], "username": username, "pin": pin}).status_code == 200
    else:
        r = client.post("/api/users", json={"username": username, "pin": pin}, headers=auth_headers())
        assert r.status_code == 200, r.text
    return username


def test_owner_creates_and_lists_user():
    _create_user("pepe")
    users = client.get("/api/users", headers=auth_headers()).json()["users"]
    pepe = next(u for u in users if u["username"] == "pepe")
    assert pepe["is_owner"] is False
    admin = next(u for u in users if u["username"] == "admin")
    assert admin["is_owner"] is True


def test_owner_resets_user_pin():
    _create_user("ana")
    uid = next(u["id"] for u in client.get("/api/users", headers=auth_headers()).json()["users"] if u["username"] == "ana")
    assert client.post(f"/api/users/{uid}/reset-pin", json={"pin": "9999"}, headers=auth_headers()).status_code == 200
    assert client.post("/api/login", json={"username": "ana", "pin": "9999"}).status_code == 200
    assert client.post("/api/login", json={"username": "ana", "pin": "2222"}).status_code == 401


def test_duplicate_username_rejected():
    assert client.post("/api/users", json={"username": "admin", "pin": "1234"}, headers=auth_headers()).status_code == 400


def test_non_owner_cannot_manage_users():
    _create_user("bety")
    bety = auth_headers("bety", "2222")
    assert client.get("/api/users", headers=bety).status_code == 403
    assert client.post("/api/users", json={"username": "x", "pin": "1111"}, headers=bety).status_code == 403
    assert client.post("/api/database/clear", headers=bety).status_code == 403


def test_register_with_valid_invite():
    inv = client.post("/api/invites", headers=auth_headers()).json()
    r = client.post("/api/register", json={"code": inv["code"], "username": "luis", "pin": "3333"})
    assert r.status_code == 200
    body = client.get("/api/invites", headers=auth_headers()).json()["invites"]
    used = next(i for i in body if i["code"] == inv["code"])
    assert used["used"] is True
    assert used["used_by"] == "luis"


def test_register_requires_valid_or_unused_code():
    assert client.post("/api/register", json={"code": "NOEXISTE", "username": "x1", "pin": "1234"}).status_code == 400
    inv = client.post("/api/invites", headers=auth_headers()).json()
    client.post("/api/register", json={"code": inv["code"], "username": "x1", "pin": "1234"})
    assert client.post("/api/register", json={"code": inv["code"], "username": "x2", "pin": "1234"}).status_code == 400


def test_register_duplicate_username_rejected():
    inv = client.post("/api/invites", headers=auth_headers()).json()
    assert client.post("/api/register", json={"code": inv["code"], "username": "admin", "pin": "1234"}).status_code == 400


def test_invites_list_includes_used_by():
    inv = client.post("/api/invites", headers=auth_headers()).json()
    pendientes = client.get("/api/invites", headers=auth_headers()).json()["invites"]
    libre = next(i for i in pendientes if i["code"] == inv["code"])
    assert libre["used"] is False
    assert libre["used_by"] is None
    reg = client.post("/api/register", json={"code": inv["code"], "username": "nuevo_inv", "pin": "3333"})
    assert reg.status_code == 200, reg.text
    body = client.get("/api/invites", headers=auth_headers()).json()["invites"]
    usada = next(i for i in body if i["code"] == inv["code"])
    assert usada["used"] is True
    assert usada["used_by"] == "nuevo_inv"


def test_settings_lista_invitaciones_oculta_por_defecto():
    r = client.get("/settings", headers=auth_headers())
    assert '<button type="button" class="link-toggle" id="inviteToggle"' in r.text
    assert ">Mostrar invitaciones</button>" in r.text
    assert '<div id="inviteList" class="invite-list" style="display:none"></div>' in r.text
    assert "function toggleInviteList()" in r.text
    assert "'Ocultar invitaciones'" in r.text
    assert "<th>Codigo invitacion</th><th>Quien lo ha usado</th><th>usado (S/N)</th>" in r.text
    assert "used-tag" not in r.text


def test_invite_endpoints_owner_only():
    assert anon.get("/api/invites").status_code == 401
    assert anon.post("/api/invites").status_code == 401
    _create_user("carlos")
    carlos = auth_headers("carlos", "2222")
    assert client.get("/api/invites", headers=carlos).status_code == 403
    assert client.post("/api/invites", headers=carlos).status_code == 403


def test_delete_user_removes_its_entries_and_settings():
    _create_user("diana")
    diana = auth_headers("diana", "2222")
    client.post("/api/entries/manual", json={"date": "2027-06-09", "time_in": "09:00:00", "time_out": "13:00:00"}, headers=diana)
    client.post("/api/settings", json={"weekly_hours": 32}, headers=diana)
    uid = next(u["id"] for u in client.get("/api/users", headers=auth_headers()).json()["users"] if u["username"] == "diana")
    assert client.delete(f"/api/users/{uid}", headers=auth_headers()).status_code == 200
    assert client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"] == []
    assert client.post("/api/login", json={"username": "diana", "pin": "2222"}).status_code == 401


def test_cannot_delete_owner():
    uid = next(u["id"] for u in client.get("/api/users", headers=auth_headers()).json()["users"] if u["username"] == "admin")
    assert client.delete(f"/api/users/{uid}", headers=auth_headers()).status_code == 400


def test_settings_per_user_isolation():
    client.post("/api/settings", json={"weekly_hours": 35}, headers=auth_headers())
    _create_user("eva")
    eva = auth_headers("eva", "2222")
    client.post("/api/settings", json={"weekly_hours": 40}, headers=eva)
    assert client.get("/api/settings", headers=auth_headers()).json()["weekly_hours"] == 35.0
    assert client.get("/api/settings", headers=eva).json()["weekly_hours"] == 40.0


def test_entries_isolation_between_users():
    client.post("/api/entries/manual", json={"date": "2027-06-10", "time_in": "08:00:00", "time_out": "12:00:00"}, headers=auth_headers())
    _create_user("frank")
    frank = auth_headers("frank", "2222")
    client.post("/api/entries/manual", json={"date": "2027-06-11", "time_in": "08:00:00", "time_out": "12:00:00"}, headers=frank)
    admin_entries = client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]
    frank_entries = client.get("/api/entries?month=6&year=2027", headers=frank).json()["entries"]
    assert [e["date"] for e in admin_entries] == ["2027-06-10"]
    assert [e["date"] for e in frank_entries] == ["2027-06-11"]
    assert client.put(f"/api/entries/{admin_entries[0]['id']}", json={"date": "2027-06-12", "time_in": "08:00:00", "time_out": "12:00:00"}, headers=frank).status_code == 404
    assert client.delete(f"/api/entries/{admin_entries[0]['id']}", headers=frank).status_code == 404


# ------------------------------------------------------------------
# Paginas
# ------------------------------------------------------------------
def test_pages_public_and_auth():
    assert client.get("/login").status_code == 200
    r = client.get("/dashboard", headers=auth_headers())
    assert r.status_code == 200 and "Control Horario" in r.text
    for path in ["/calendar", "/history", "/reports", "/settings"]:
        assert client.get(path, headers=auth_headers()).status_code == 200


def test_root_redirects_to_dashboard():
    assert anon.get("/", follow_redirects=False).status_code in (303, 307, 302)


def test_nav_brand_links_to_dashboard():
    for path in ["/dashboard", "/calendar", "/history", "/reports", "/settings"]:
        r = client.get(path, headers=auth_headers())
        assert '<a href="/dashboard" class="nav-brand">Control Horario</a>' in r.text


def test_menu_usuario_contiene_secciones_y_logout():
    for path in ["/dashboard", "/calendar", "/history", "/reports", "/settings"]:
        r = client.get(path, headers=auth_headers())
        assert "nav-toggle" not in r.text
        assert "nav-links" not in r.text
        assert "toggleNav" not in r.text
        for href, label in [("/dashboard", "Inicio"), ("/calendar", "Calendario"),
                            ("/history", "Historial"), ("/reports", "Informes"),
                            ("/settings", "Ajustes")]:
            assert f"['{href}', '{label}']" in r.text
        assert '<a href="/api/logout" class="menu-logout">Cerrar sesion</a>' in r.text


def test_settings_shows_app_version():
    r = client.get("/settings", headers=auth_headers())
    assert app_main.APP_VERSION == "1.0"
    assert f"{app_main.APP_NAME} v.{app_main.APP_VERSION}" in r.text


def test_pwa_name_is_control_de_horario_age():
    manifest = Path(app_main.__file__).parent / "static" / "manifest.json"
    data = json.loads(manifest.read_text())
    assert data["name"] == app_main.APP_NAME
    assert data["short_name"] == app_main.APP_NAME


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------
def test_settings_defaults():
    d = client.get("/api/settings", headers=auth_headers()).json()
    assert d["weekly_hours"] == 35.0
    s = d["schedule"]
    assert s["morning_start"] == "07:00"
    assert s["afternoon_end"] == "19:00"
    assert s["obj_tarde_5d"] == "03:00"
    assert s["obj_tarde_4d"] == "02:30"
    assert s["obj_tarde_3d"] == "01:45"
    assert s["obj_tarde_2d"] == "01:00"
    assert s["obj_tarde_1d"] == "00:00"


def test_settings_save_and_persist():
    r = client.post("/api/settings", json={
        "weekly_hours": 37,
        "telework_pct": 40,
        "default_telework_days": "1,3",
        "obj_tarde_4d": "02:00",
    }, headers=auth_headers())
    assert r.status_code == 200
    d = client.get("/api/settings", headers=auth_headers()).json()
    assert d["weekly_hours"] == 37.0
    assert d["telework_pct"] == 40.0
    assert d["presencial_pct"] == 60.0
    assert d["default_telework_days"] == [1, 3]
    assert d["schedule"]["obj_tarde_4d"] == "02:00"


def test_settings_invalid_time_format():
    r = client.post("/api/settings", json={"morning_start": "25:99"}, headers=auth_headers())
    assert r.status_code == 400


def test_settings_unauthorized():
    assert anon.post("/api/settings", json={"weekly_hours": 30}).status_code == 401


# ------------------------------------------------------------------
# Creacion / validacion de registros
# ------------------------------------------------------------------
def test_create_presencial_entry():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "time_in": "07:00:00", "time_out": "19:00:00",
        "entry_type": "presencial",
    }, headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_create_without_time_in_rejected():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "entry_type": "presencial",
    }, headers=auth_headers())
    assert r.status_code == 400


def test_create_outside_work_window_rejected():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "time_in": "06:00:00", "time_out": "07:00:00",
    }, headers=auth_headers())
    assert r.status_code == 400
    assert "de entrada" in r.json()["detail"]


def test_create_exit_before_entry_rejected():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "time_in": "09:00:00", "time_out": "08:00:00",
    }, headers=auth_headers())
    assert r.status_code == 400
    assert "posterior" in r.json()["detail"]


def test_create_open_entry_allowed():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "time_in": "07:00:00", "time_out": None,
        "entry_type": "presencial",
    }, headers=auth_headers())
    assert r.status_code == 200


def test_create_special_day_mark():
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "entry_type": "festivo",
    }, headers=auth_headers())
    assert r.status_code == 200


def test_create_work_on_special_day_rejected():
    client.post("/api/entries/manual", json={"date": "2027-06-07", "entry_type": "festivo"}, headers=auth_headers())
    r = client.post("/api/entries/manual", json={
        "date": "2027-06-07", "time_in": "07:00:00", "time_out": "15:00:00",
    }, headers=auth_headers())
    assert r.status_code == 400
    assert "marcado" in r.json()["detail"]


def test_create_second_special_mark_rejected():
    client.post("/api/entries/manual", json={"date": "2027-06-07", "entry_type": "vacaciones"}, headers=auth_headers())
    r = client.post("/api/entries/manual", json={"date": "2027-06-07", "entry_type": "ap"}, headers=auth_headers())
    assert r.status_code == 400


def test_normalize_entry_type_variants():
    for raw, expected in [("tele", "teletrabajo"), ("TELEtrabajo", "teletrabajo"),
                          ("vacaciones", "vacaciones"), ("ap", "ap"), ("otro", "otro"),
                          ("festivo", "festivo"), ("presencial", "presencial")]:
        assert app_main.normalize_entry_type(raw) == expected


# ------------------------------------------------------------------
# Edicion / borrado
# ------------------------------------------------------------------
def _make(date_str, t_in="07:00:00", t_out="15:00:00", etype="presencial"):
    return client.post("/api/entries/manual", json={
        "date": date_str, "time_in": t_in, "time_out": t_out, "entry_type": etype,
    }, headers=auth_headers()).json()["id"]


def test_update_entry():
    eid = _make("2027-06-07", "07:00:00", "15:00:00")
    r = client.put(f"/api/entries/{eid}", json={
        "date": "2027-06-08", "time_in": "08:00:00", "time_out": "16:00:00",
        "entry_type": "teletrabajo", "notes": "nueva",
    }, headers=auth_headers())
    assert r.status_code == 200
    ent = client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]
    assert ent[0]["date"] == "2027-06-08"
    assert ent[0]["entry_type"] == "teletrabajo"
    assert ent[0]["notes"] == "nueva"


def test_update_festivo_to_other_rejected():
    eid = _make("2027-06-07", etype="festivo")
    r = client.put(f"/api/entries/{eid}", json={
        "date": "2027-06-07", "entry_type": "presencial", "time_in": "07:00:00", "time_out": "15:00:00",
    }, headers=auth_headers())
    assert r.status_code == 400


def test_update_missing_entry_404():
    r = client.put("/api/entries/99999", json={"date": "2027-06-07", "entry_type": "presencial"}, headers=auth_headers())
    assert r.status_code == 404


def test_delete_entry():
    eid = _make("2027-06-07")
    assert client.delete(f"/api/entries/{eid}", headers=auth_headers()).status_code == 200
    assert client.delete(f"/api/entries/{eid}", headers=auth_headers()).status_code == 404


# ------------------------------------------------------------------
# Logica de horario (unitarias)
# ------------------------------------------------------------------
def _sch():
    return {
        "morning_start": app_main.parse_time("07:00"),
        "morning_end": app_main.parse_time("14:30"),
        "afternoon_start": app_main.parse_time("14:31"),
        "afternoon_end": app_main.parse_time("19:00"),
        "lunch_start": app_main.parse_time("16:00"),
        "lunch_end": app_main.parse_time("16:30"),
        "obj_tarde_5d": app_main.parse_time("03:00"),
        "obj_tarde_4d": app_main.parse_time("02:30"),
        "obj_tarde_3d": app_main.parse_time("01:45"),
        "obj_tarde_2d": app_main.parse_time("01:00"),
        "obj_tarde_1d": app_main.parse_time("00:00"),
    }


def test_lunch_deducted_rules():
    sch = _sch()
    cases = [
        ("16:20", "18:00", 10),
        ("16:10", "17:00", 20),
        ("15:00", "16:29", 0),
        ("07:50", "19:00", 30),
        ("15:00", "16:30", 30),
        ("16:31", "18:00", 0),
    ]
    for ti, to, expected in cases:
        assert app_main.lunch_deducted_minutes(app_main.parse_time(ti), app_main.parse_time(to), sch) == expected


def test_lunch_vacio_no_descuenta():
    sch = dict(_sch())
    sch["lunch_start"] = None
    sch["lunch_end"] = None
    assert app_main.lunch_deducted_minutes(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch) == 0
    net, lunch = app_main.entry_net(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch)
    assert round(net, 2) == 12.0
    assert lunch == 0.0
    m, a, l = app_main.entry_split(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch)
    assert round(m, 2) == 7.5
    assert round(a, 2) == 4.5
    assert l == 0.0
    sch["lunch_start"] = app_main.parse_time("16:00")
    assert app_main.lunch_deducted_minutes(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch) == 0


def test_guardar_comida_vacia_sin_descuento():
    r = client.post("/api/settings", json={"lunch_start": "", "lunch_end": ""}, headers=auth_headers())
    assert r.status_code == 200
    s = client.get("/api/settings", headers=auth_headers()).json()["schedule"]
    assert s["lunch_start"] == "" and s["lunch_end"] == ""
    client.post("/api/entries/manual", json={"date": "2027-03-01", "time_in": "07:00", "time_out": "19:00"}, headers=auth_headers())
    d = client.get("/api/day?date_str=2027-03-01", headers=auth_headers()).json()
    assert d["lunch_deducted"] == "0h 00min"
    assert d["total_hours"] == "12h 00min"
    assert d["morning_hours"] == "7h 30min"
    assert d["afternoon_hours"] == "4h 30min"
    w = client.get("/api/week?week_start=2027-03-01", headers=auth_headers()).json()
    assert w["lunch_deducted_week"] == 0
    assert round(w["total_week"], 2) == 12.0
    assert w["lunch_deducted_formatted"] == "0h 00min"


def test_comida_solo_un_campo_vacio_no_descuenta():
    assert client.post("/api/settings", json={"lunch_start": "16:00", "lunch_end": ""}, headers=auth_headers()).status_code == 200
    client.post("/api/entries/manual", json={"date": "2027-03-08", "time_in": "07:00", "time_out": "19:00"}, headers=auth_headers())
    assert client.get("/api/day?date_str=2027-03-08", headers=auth_headers()).json()["lunch_deducted"] == "0h 00min"
    assert client.post("/api/settings", json={"lunch_start": "16:00", "lunch_end": "16:30"}, headers=auth_headers()).status_code == 200
    assert client.get("/api/day?date_str=2027-03-08", headers=auth_headers()).json()["lunch_deducted"] == "0h 30min"


def test_horario_invalido_sigue_dando_error():
    assert client.post("/api/settings", json={"morning_start": ""}, headers=auth_headers()).status_code == 400
    assert client.post("/api/settings", json={"lunch_start": "25:99"}, headers=auth_headers()).status_code == 400


def test_entry_net_and_split():
    sch = _sch()
    net, lunch = app_main.entry_net(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch)
    assert round(net, 2) == 11.5
    assert round(lunch, 2) == 0.5
    m, a, l = app_main.entry_split(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch)
    assert round(m, 2) == 7.5
    assert round(a, 2) == 4.0
    assert round(l, 2) == 0.5
    mg, ag, lg = app_main.entry_split_gross(app_main.parse_time("07:00"), app_main.parse_time("19:00"), sch)
    assert round(mg, 2) == 7.5
    assert round(ag, 2) == 4.5
    assert round(lg, 2) == 0.5


def test_afternoon_objective_mapping():
    sch = _sch()
    expected = {0: 0.0, 1: 0.0, 2: 1.0, 3: 1.75, 4: 2.5, 5: 3.0, 6: 0.0}
    for n, exp in expected.items():
        assert app_main.afternoon_objective_hours(n, sch) == exp


def test_entry_shift():
    sch = _sch()
    assert app_main.entry_shift(app_main.parse_time("07:00"), sch) == "manana"
    assert app_main.entry_shift(app_main.parse_time("14:31"), sch) == "tarde"
    assert app_main.entry_shift(app_main.parse_time("16:00"), sch) == "tarde"


# ------------------------------------------------------------------
# /api/week (objetivo prorrateado, especiales, totales)
# ------------------------------------------------------------------
def _seed_week():
    # Semana del 2027-06-07 (lunes): Lunes festivo, M-J trabajados, V sin registro
    _make("2027-06-07", etype="festivo")
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")   # 11.5h netas
    _make("2027-06-09", "08:00:00", "17:00:00", "teletrabajo")  # 8.5h netas
    _make("2027-06-10", "07:00:00", "14:30:00", "presencial")   # 7.5h netas


def _seed_week_festivo_4trabajados():
    # Lunes festivo + Martes a Viernes trabajados (07:00-12:00 = 5h netas c/u)
    _make("2027-06-07", etype="festivo")
    for day in (8, 9, 10, 11):
        _make(f"2027-06-{day:02d}", "07:00:00", "12:00:00", "presencial")


def test_week_with_festivo_prorates_objective():
    _seed_week()
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 3
    assert d["non_worked_days"] == 1
    assert d["afternoon_available_days"] == 4
    assert any(day["special"] == "festivo" for day in d["days"])
    assert d["afternoon_objective_formatted"] == "2h 30min"
    assert d["afternoon_reduction_formatted"] == "7h 00min"
    assert d["target_formatted"] == "28h 00min"
    assert d["total_week_formatted"] == "27h 30min"
    assert d["remaining_formatted"] == "0h 30min"
    assert d["morning_formatted"] == "21h 30min"
    assert d["afternoon_formatted"] == "6h 00min"
    assert d["lunch_deducted_formatted"] == "1h 00min"
    assert d["teletrabajo_formatted"] == "8h 30min"
    assert d["presencial_formatted"] == "19h 00min"


def test_week_festivo_descuenta_dia_completo_sin_horas():
    _seed_week_festivo_4trabajados()
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 4
    assert d["non_worked_days"] == 1
    assert d["target_base"] == 35.0
    assert d["target_formatted"] == "28h 00min"
    assert d["afternoon_reduction_formatted"] == "7h 00min"
    assert d["remaining_formatted"] == "8h 00min"


def test_week_descuento_dia_completo_se_escala_con_horas_semanales():
    client.post("/api/settings", json={"weekly_hours": "40"}, headers=auth_headers())
    _seed_week_festivo_4trabajados()
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 4
    assert d["target_formatted"] == "32h 00min"
    assert d["afternoon_reduction_formatted"] == "8h 00min"
    assert d["remaining_formatted"] == "12h 00min"


def test_week_dos_dias_no_trabajados_descuenta_dos_jornadas():
    _make("2027-06-07", etype="festivo")
    _make("2027-06-08", etype="vacaciones")
    _make("2027-06-09", "07:00:00", "15:00:00", "presencial")
    _make("2027-06-10", "07:00:00", "15:00:00", "teletrabajo")
    _make("2027-06-11", "07:00:00", "15:00:00", "presencial")
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 3
    assert d["non_worked_days"] == 2
    assert d["afternoon_available_days"] == 3
    assert d["afternoon_objective_formatted"] == "1h 45min"
    assert d["target_formatted"] == "21h 00min"
    assert d["afternoon_reduction_formatted"] == "14h 00min"


def test_week_normal_five_days_no_discount():
    dates = ["2027-05-31", "2027-06-01", "2027-06-02", "2027-06-03", "2027-06-04"]
    for dd in dates:
        _make(dd, "07:00:00", "15:00:00")
    d = client.get("/api/week?week_start=2027-05-31", headers=auth_headers()).json()
    assert d["days_worked"] == 5
    assert d["non_worked_days"] == 0
    assert d["target_formatted"] == "35h 00min"
    assert d["afternoon_reduction_formatted"] == "0h 00min"


def test_week_uses_custom_objective_from_settings():
    client.post("/api/settings", json={"obj_tarde_4d": "02:00"}, headers=auth_headers())
    _seed_week_festivo_4trabajados()
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 4
    assert d["afternoon_objective_formatted"] == "2h 00min"
    assert d["afternoon_reduction_formatted"] == "7h 00min"
    assert d["target_formatted"] == "28h 00min"


def test_week_solo_dias_trabajados_cuenta():
    # Semana a medias: solo Lunes y Martes registrados -> "2 dias trabajados"
    _make("2027-06-07", "07:00:00", "12:00:00", "presencial")
    _make("2027-06-08", "07:00:00", "12:00:00", "teletrabajo")
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 2
    assert d["afternoon_available_days"] == 5
    assert d["afternoon_objective_formatted"] == "3h 00min"
    assert d["target_formatted"] == "35h 00min"
    assert d["remaining_formatted"] == "25h 00min"


def test_week_no_marks_zero_worked_days():
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    assert d["days_worked"] == 0
    assert d["non_worked_days"] == 0
    assert d["afternoon_available_days"] == 5
    assert d["afternoon_objective_formatted"] == "3h 00min"
    assert d["target_formatted"] == "35h 00min"
    assert d["remaining_formatted"] == "35h 00min"


def _fmt_to_min(s):
    h, m = int(s.split("h ")[0]), int(s.split("h ")[1].replace("min", ""))
    return h * 60 + m


def test_week_total_coincide_con_suma_de_tarjetas_de_dia():
    # Con segundos en las entradas, el total de semana debe coincidir
    # exactamente con la suma de los totales mostrados por dia.
    _make("2027-06-07", "07:00:15", "18:59:45", "teletrabajo")
    _make("2027-06-08", "07:45:30", "15:15:50", "presencial")
    _make("2027-06-09", "09:00:00", "18:30:00", "presencial")
    d = client.get("/api/week?week_start=2027-06-07", headers=auth_headers()).json()
    dias_min = sum(_fmt_to_min(day["total_formatted"]) for day in d["days"])
    assert d["total_week_formatted"] == f"{dias_min // 60}h {dias_min % 60:02d}min"
    assert abs(d["total_week"] - sum(day["total"] for day in d["days"])) < 0.01


# ------------------------------------------------------------------
# /api/day y /api/today
# ------------------------------------------------------------------
def test_day_summary_gross_breakdown():
    _make("2027-06-08", "07:00:00", "19:00:00")
    d = client.get("/api/day?date_str=2027-06-08", headers=auth_headers()).json()
    assert d["morning_hours"] == "7h 30min"
    assert d["afternoon_hours"] == "4h 30min"
    assert d["lunch_deducted"] == "0h 30min"
    assert d["total_hours"] == "11h 30min"
    assert len(d["entries"]) == 1
    e = d["entries"][0]
    assert e["shift"] == "manana"
    assert e["has_morning"] is True
    assert e["has_afternoon"] is True


def test_day_summary_open_entry():
    client.post("/api/entries/manual", json={
        "date": "2027-06-08", "time_in": "07:00:00", "time_out": None,
    }, headers=auth_headers())
    d = client.get("/api/day?date_str=2027-06-08", headers=auth_headers()).json()
    assert d["total_hours"] == "0h 00min"
    assert len(d["entries"]) == 1


def test_today_summary_net():
    today = date.today().isoformat()
    client.post("/api/entries/manual", json={
        "date": today, "time_in": "07:00:00", "time_out": "19:00:00",
    }, headers=auth_headers())
    d = client.get("/api/today", headers=auth_headers()).json()
    assert d["date"] == today
    assert d["total_hours"] == "11h 30min"
    assert d["morning_hours"] == "7h 30min"
    assert d["afternoon_hours"] == "4h 00min"


# ------------------------------------------------------------------
# Historial (/api/entries)
# ------------------------------------------------------------------
def test_entries_list_month_year():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    _make("2027-06-09", "08:00:00", "17:00:00", "teletrabajo")
    r = client.get("/api/entries?month=6&year=2027", headers=auth_headers())
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 2
    pres = next(e for e in entries if e["entry_type"] == "presencial")
    assert pres["total_hours"] == 11.5
    assert pres["shift"] == "manana"
    tele = next(e for e in entries if e["entry_type"] == "teletrabajo")
    assert tele["total_hours"] == 8.5
    assert tele["weekday"] == "Mie"


# ------------------------------------------------------------------
# Informes / CSV
# ------------------------------------------------------------------
def test_monthly_report():
    _make("2027-06-07", etype="festivo")
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    _make("2027-06-09", "08:00:00", "17:00:00", "teletrabajo")
    r = client.get("/api/report/monthly?month=6&year=2027", headers=auth_headers())
    assert r.status_code == 200
    d = r.json()
    assert d["month"] == 6 and d["year"] == 2027
    assert d["festivo_days"] == 1
    assert d["total_month_hours"] == 20.0
    assert d["presencial_hours"] == 11.5
    assert d["teletrabajo_hours"] == 8.5
    assert len(d["weeks"]) >= 1


def test_csv_export():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    r = client.get("/api/export/csv?month=6&year=2027", headers=auth_headers())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "Fecha,Dia,Entrada,Salida,Total Horas,Turno,Tipo,Notas"
    assert len(lines) == 2
    assert ",Presencial," in lines[1]
    assert ",Manana," in lines[1]


# ------------------------------------------------------------------
# Pago de deuda pendiente: validacion de ventilana en PUT
# ------------------------------------------------------------------
def test_update_out_of_window_rejected():
    eid = _make("2027-06-07", "07:00:00", "15:00:00")
    r = client.put(f"/api/entries/{eid}", json={
        "date": "2027-06-07", "time_in": "05:00:00", "time_out": "15:00:00",
    }, headers=auth_headers())
    assert r.status_code == 400


# ------------------------------------------------------------------
# Base de Datos: eliminacion, copia de seguridad y restauracion
# ------------------------------------------------------------------
def test_backup_requires_auth():
    assert anon.get("/api/database/backup").status_code == 401
    assert anon.post("/api/database/clear").status_code == 401


def test_backup_generates_gzip_sqlite():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    r = client.get("/api/database/backup", headers=auth_headers())
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.content[:2] == b"\x1f\x8b"
    raw = gzip.decompress(r.content)
    assert raw[:16] == b"SQLite format 3\x00"


def test_database_clear_keeps_settings_and_festivos():
    r = client.post("/api/settings", json={
        "weekly_hours": 40, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    assert r.status_code == 200
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    _make("2027-06-09", "07:00:00", "15:00:00", "teletrabajo")
    _make("2027-06-10", "07:00:00", "15:00:00", "vacaciones")
    _make("2027-06-11", "07:00:00", "15:00:00", "festivo")
    r = client.post("/api/database/clear", headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["festivos_preservados"] is True
    assert client.get("/api/settings", headers=auth_headers()).json()["weekly_hours"] == 40.0
    entries = client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]
    remaining = [(e["date"], e["entry_type"]) for e in entries]
    assert ("2027-06-08", "presencial") not in remaining
    assert ("2027-06-09", "teletrabajo") not in remaining
    assert ("2027-06-10", "vacaciones") not in remaining
    assert ("2027-06-11", "festivo") in remaining


def test_database_restore_roundtrip():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    client.post("/api/settings", json={
        "weekly_hours": 40, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    bk = client.get("/api/database/backup", headers=auth_headers())
    assert bk.status_code == 200
    _make("2027-06-09", "08:00:00", "14:00:00")
    client.post("/api/settings", json={
        "weekly_hours": 35, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    r = client.post(
        "/api/database/restore",
        files={"file": ("backup.db", gzip.decompress(bk.content), "application/octet-stream")},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/settings", headers=auth_headers()).json()["weekly_hours"] == 40.0
    dates = [e["date"] for e in client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]]
    assert "2027-06-08" in dates
    assert "2027-06-09" not in dates


def test_database_restore_invalid_file():
    r = client.post(
        "/api/database/restore",
        files={"file": ("bad.bin", b"no es una base de datos", "application/octet-stream")},
        headers=auth_headers(),
    )
    assert r.status_code == 400


def test_database_restore_requires_auth():
    r = anon.post("/api/database/restore", files={"file": ("x.bin", b"zzz", "application/octet-stream")})
    assert r.status_code == 401


def test_backup_portable_json_snapshot():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    r = app_main._backup_json()
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    assert r.headers["content-disposition"].startswith("attachment")
    assert ".horarios.gz" in r.headers["content-disposition"]
    raw = gzip.decompress(r.body)
    snap = json.loads(raw.decode("utf-8"))
    assert snap["format"] == "horarios-json-1"
    assert set(snap["tables"]) >= {"users", "entries", "settings"}
    entry_rows = snap["tables"]["entries"]["rows"]
    assert any("2027-06-08" in (r2[2] if isinstance(r2[2], str) else r2[2].isoformat()) for r2 in entry_rows)


def test_restore_portable_json_roundtrip():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    client.post("/api/settings", json={
        "weekly_hours": 40, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    bk = app_main._backup_json()
    _make("2027-06-09", "08:00:00", "14:00:00")
    client.post("/api/settings", json={
        "weekly_hours": 35, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    r = client.post(
        "/api/database/restore",
        files={"file": ("backup.horarios", bk.body, "application/octet-stream")},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/settings", headers=auth_headers()).json()["weekly_hours"] == 40.0
    dates = [e["date"] for e in client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]]
    assert "2027-06-08" in dates
    assert "2027-06-09" not in dates


def test_restore_json_invalid_format():
    payload = gzip.compress(b'{"format":"otro","tables":{}}')
    r = client.post(
        "/api/database/restore",
        files={"file": ("bad.horarios", payload, "application/octet-stream")},
        headers=auth_headers(),
    )
    assert r.status_code == 400


def test_quick_backup_creates_and_lists():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    r = client.post("/api/database/backups/quick", headers=auth_headers())
    assert r.status_code == 200
    name = r.json()["backup"]
    body = client.get("/api/database/backups", headers=auth_headers()).json()["backups"]
    assert any(b["name"] == name and b["size"] > 0 for b in body)


def test_quick_backup_restore_roundtrip():
    _make("2027-06-08", "07:00:00", "19:00:00", "presencial")
    client.post("/api/settings", json={
        "weekly_hours": 40, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    name = client.post("/api/database/backups/quick", headers=auth_headers()).json()["backup"]
    _make("2027-06-09", "08:00:00", "14:00:00")
    client.post("/api/settings", json={
        "weekly_hours": 35, "telework_pct": 0, "default_telework_days": "",
    }, headers=auth_headers())
    r = client.post(f"/api/database/backups/{name}/restore", headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/settings", headers=auth_headers()).json()["weekly_hours"] == 40.0
    dates = [e["date"] for e in client.get("/api/entries?month=6&year=2027", headers=auth_headers()).json()["entries"]]
    assert "2027-06-08" in dates
    assert "2027-06-09" not in dates


def test_delete_quick_backup():
    name = client.post("/api/database/backups/quick", headers=auth_headers()).json()["backup"]
    assert client.delete(f"/api/database/backups/{name}", headers=auth_headers()).status_code == 200
    body = client.get("/api/database/backups", headers=auth_headers()).json()["backups"]
    assert all(b["name"] != name for b in body)
    assert client.delete(f"/api/database/backups/{name}", headers=auth_headers()).status_code == 404
    assert client.post(f"/api/database/backups/{name}/restore", headers=auth_headers()).status_code == 404


def test_backup_management_requires_owner_and_auth():
    name = client.post("/api/database/backups/quick", headers=auth_headers()).json()["backup"]
    _create_user("gina")
    gina = auth_headers("gina", "2222")
    assert anon.get("/api/database/backups").status_code == 401
    assert anon.post("/api/database/backups/quick").status_code == 401
    assert client.get("/api/database/backups", headers=gina).status_code == 403
    assert client.post("/api/database/backups/quick", headers=gina).status_code == 403
    assert client.post(f"/api/database/backups/{name}/restore", headers=gina).status_code == 403
    assert client.delete(f"/api/database/backups/{name}", headers=gina).status_code == 403
    assert client.post("/api/database/backups/invalid-name!/restore", headers=auth_headers()).status_code == 400
    assert client.post("/api/database/backups/..%2Fesc/restore", headers=auth_headers()).status_code in (400, 404)


def test_download_quick_backup():
    name = client.post("/api/database/backups/quick", headers=auth_headers()).json()["backup"]
    r = client.get(f"/api/database/backups/{name}/download", headers=auth_headers())
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    assert r.content[:2] == b"\x1f\x8b"
    snap = json.loads(gzip.decompress(r.content).decode("utf-8"))
    assert snap["format"] == "horarios-json-1"