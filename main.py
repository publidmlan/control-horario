import os
import csv
import io
import gzip
import sqlite3
from datetime import date, time, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Response, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import extract, text, inspect
from pydantic import BaseModel
import bcrypt

from database import engine, get_db, Base
from models import User, Entry, Setting

app = FastAPI(title="Control Horario")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

PIN_DEFAULT = os.getenv("APP_PIN", "1234")

SPECIAL_TYPES = {"festivo", "vacaciones", "ap", "otro"}
NON_WORK_TYPES = {"festivo", "vacaciones", "ap"}
TYPE_LABELS = {
    "presencial": "Presencial",
    "teletrabajo": "Teletrabajo",
    "festivo": "Festivo",
    "vacaciones": "Vacaciones",
    "ap": "Asuntos Propios",
    "otro": "Otro",
}
SCHEDULE_DEFAULTS = {
    "morning_start": "07:00",
    "morning_end": "14:30",
    "afternoon_start": "14:31",
    "afternoon_end": "19:00",
    "lunch_start": "16:00",
    "lunch_end": "16:30",
    "obj_tarde_5d": "03:00",
    "obj_tarde_4d": "02:30",
    "obj_tarde_3d": "01:45",
    "obj_tarde_2d": "01:00",
    "obj_tarde_1d": "00:00",
}


def init_db():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("entries")]
    if cols and "entry_type" not in cols:
        with engine.connect() as conn:
            conn.execute(text('ALTER TABLE entries ADD COLUMN entry_type VARCHAR(20) DEFAULT "presencial" NOT NULL'))
            conn.commit()
    db = next(get_db())
    if db.query(User).count() == 0:
        pin_hash = bcrypt.hashpw(PIN_DEFAULT.encode(), bcrypt.gensalt()).decode()
        db.add(User(pin_hash=pin_hash))
        db.commit()
    if not db.query(Setting).filter(Setting.key == "weekly_hours").first():
        db.add(Setting(key="weekly_hours", value="35"))
        db.commit()
    seeded = seed_national_holidays(db)
    if seeded:
        print(f"[init] {seeded} festivos nacionales anadidos")
    db.close()


def get_setting(db: Session, key: str, default: str = "") -> str:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else default


@app.on_event("startup")
def startup():
    init_db()


class ManualEntryRequest(BaseModel):
    date: str
    time_in: Optional[str] = None
    time_out: Optional[str] = None
    entry_type: Optional[str] = "presencial"


class EntryUpdate(BaseModel):
    date: str
    time_in: Optional[str] = None
    time_out: Optional[str] = None
    notes: Optional[str] = None
    entry_type: Optional[str] = None


def normalize_entry_type(t: str) -> str:
    t = (t or "").strip().lower()
    if "tele" in t:
        return "teletrabajo"
    if "vacac" in t:
        return "vacaciones"
    if "asuntos" in t or t == "ap":
        return "ap"
    if "otro" in t:
        return "otro"
    if "fest" in t:
        return "festivo"
    return "presencial"


def _validate_day_mark(db: Session, d: date, entry_type: str, exclude_id: int = None):
    q = db.query(Entry).filter(Entry.date == d)
    if exclude_id:
        q = q.filter(Entry.id != exclude_id)
    existing = q.all()
    if entry_type in SPECIAL_TYPES:
        if existing:
            return "Este dia ya tiene registros. Desmarquelo o eliminelos primero"
    else:
        for e in existing:
            if e.entry_type in SPECIAL_TYPES:
                label = TYPE_LABELS.get(e.entry_type, e.entry_type).lower()
                return f"Este dia esta marcado como {label} y no admite registros"
    return None


class PinRequest(BaseModel):
    pin: str


class SettingRequest(BaseModel):
    value: str


class SettingsUpdate(BaseModel):
    weekly_hours: Optional[float] = None
    telework_pct: Optional[float] = None
    default_telework_days: Optional[str] = None
    morning_start: Optional[str] = None
    morning_end: Optional[str] = None
    afternoon_start: Optional[str] = None
    afternoon_end: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    obj_tarde_5d: Optional[str] = None
    obj_tarde_4d: Optional[str] = None
    obj_tarde_3d: Optional[str] = None
    obj_tarde_2d: Optional[str] = None
    obj_tarde_1d: Optional[str] = None


def check_auth(request: Request):
    if request.cookies.get("auth_token") != "authenticated":
        raise HTTPException(status_code=401, detail="No autenticado")


def parse_time(t: str) -> time:
    parts = t.strip().split(":")
    return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def fmt_hours(h: float) -> str:
    h_int = int(h)
    m = int((h - h_int) * 60)
    return f"{h_int}h {m:02d}min"


def _entry_hours(e: dict, sch: dict = None) -> float:
    if not e.get("time_out"):
        return 0
    ti = parse_time(e["time_in"])
    to = parse_time(e["time_out"])
    if sch:
        net, _ov = entry_net(ti, to, sch)
        return net
    return gross_minutes(ti, to) / 60.0


def get_week_number(d: date) -> int:
    return d.isocalendar()[1]


def parse_telework_days(raw: str) -> list:
    try:
        return sorted({int(x) for x in raw.split(",") if x.strip() != ""})
    except ValueError:
        return []


def get_telework_pct(db: Session) -> float:
    return max(0.0, min(100.0, float(get_setting(db, "telework_pct", "0"))))


def minutes_of(t: time) -> int:
    return t.hour * 60 + t.minute


def gross_minutes(ti: time, to: time) -> int:
    return max(0, minutes_of(to) - minutes_of(ti))


def overlap_minutes(a_in: time, a_out: time, b_in: time, b_out: time) -> int:
    return max(0, min(minutes_of(a_out), minutes_of(b_out)) - max(minutes_of(a_in), minutes_of(b_in)))


def get_schedule(db: Session) -> dict:
    def tm(key: str, default: str) -> time:
        try:
            return parse_time(get_setting(db, key, default))
        except Exception:
            return parse_time(default)
    sch = {
        "morning_start": tm("morning_start", SCHEDULE_DEFAULTS["morning_start"]),
        "morning_end": tm("morning_end", SCHEDULE_DEFAULTS["morning_end"]),
        "afternoon_start": tm("afternoon_start", SCHEDULE_DEFAULTS["afternoon_start"]),
        "afternoon_end": tm("afternoon_end", SCHEDULE_DEFAULTS["afternoon_end"]),
        "lunch_start": tm("lunch_start", SCHEDULE_DEFAULTS["lunch_start"]),
        "lunch_end": tm("lunch_end", SCHEDULE_DEFAULTS["lunch_end"]),
    }
    for i in range(1, 6):
        key = f"obj_tarde_{i}d"
        sch[key] = tm(key, SCHEDULE_DEFAULTS[key])
    return sch


def lunch_deducted_minutes(ti: time, to: time, sch: dict) -> int:
    if ti is None or to is None:
        return 0
    ls = sch["lunch_start"]
    le = sch["lunch_end"]
    if ls >= le:
        return 0
    if to < le:
        return 0
    effective_in = max(ti, ls)
    if effective_in >= le:
        return 0
    return minutes_of(le) - minutes_of(effective_in)


def entry_net(ti: time, to: time, sch: dict):
    if ti is None or to is None:
        return 0.0, 0.0
    lunch_ov = lunch_deducted_minutes(ti, to, sch)
    net = gross_minutes(ti, to) - lunch_ov
    return net / 60.0, lunch_ov / 60.0


def entry_split(ti: time, to: time, sch: dict):
    if ti is None or to is None:
        return 0.0, 0.0, 0.0
    ms, me = sch["morning_start"], sch["morning_end"]
    ae = sch["afternoon_end"]
    ls, le = sch["lunch_start"], sch["lunch_end"]

    def ov(a: time, b: time) -> float:
        return overlap_minutes(ti, to, a, b) / 60.0

    lunch_min = lunch_deducted_minutes(ti, to, sch)
    morning_lunch = min(overlap_minutes(ls, le, ms, me), lunch_min) if lunch_min > 0 else 0
    afternoon_lunch = lunch_min - morning_lunch
    morning = max(0.0, ov(ms, me) - morning_lunch / 60.0)
    afternoon = max(0.0, ov(me, ae) - afternoon_lunch / 60.0)
    return morning, afternoon, lunch_min / 60.0


def entry_split_gross(ti: time, to: time, sch: dict):
    if ti is None or to is None:
        return 0.0, 0.0, 0.0
    ms, me = sch["morning_start"], sch["morning_end"]
    ae = sch["afternoon_end"]
    morning = overlap_minutes(ti, to, ms, me) / 60.0
    afternoon = overlap_minutes(ti, to, me, ae) / 60.0
    lunch = lunch_deducted_minutes(ti, to, sch) / 60.0
    return morning, afternoon, lunch


def entry_shift(ti: time, sch: dict) -> str:
    return "tarde" if ti >= sch["afternoon_start"] else "manana"


def afternoon_objective_hours(days_worked: int, sch: dict) -> float:
    key = {
        1: "obj_tarde_1d",
        2: "obj_tarde_2d",
        3: "obj_tarde_3d",
        4: "obj_tarde_4d",
        5: "obj_tarde_5d",
    }.get(days_worked)
    if key is None:
        return 0.0
    return minutes_of(sch[key]) / 60.0


def fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


def validate_work_window(ti: time, to: time, sch: dict):
    min_t = sch["morning_start"]
    max_t = sch["afternoon_end"]
    low = fmt_time(min_t)
    high = fmt_time(max_t)
    if ti < min_t or ti > max_t:
        return f"La hora de entrada debe estar entre {low} y {high}"
    if to and (to < min_t or to > max_t):
        return f"La hora de salida debe estar entre {low} y {high}"
    if to and to <= ti:
        return "La hora de salida debe ser posterior a la entrada"
    return None


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def national_holidays_for_year(year: int) -> list:
    fixed = [
        (1, 1),    # Ano Nuevo
        (1, 6),    # Epifania del Senor
        (5, 1),    # Fiesta del Trabajo
        (8, 15),   # Asuncion de la Virgen
        (10, 12),  # Fiesta Nacional de Espana
        (11, 1),   # Todos los Santos
        (12, 6),   # Dia de la Constitucion
        (12, 8),   # Inmaculada Concepcion
        (12, 25),  # Natividad del Senor
    ]
    days = [date(year, m, d) for m, d in fixed]
    easter = easter_sunday(year)
    days.append(easter - timedelta(days=2))  # Viernes Santo
    return days


def seed_national_holidays(db: Session) -> int:
    today = date.today()
    seeded = 0
    for y in range(today.year - 1, today.year + 3):
        for hd in national_holidays_for_year(y):
            if hd.weekday() >= 5:
                continue
            existing = db.query(Entry).filter(Entry.date == hd).first()
            if existing:
                continue
            db.add(Entry(date=hd, time_in=time(0, 0), time_out=None, entry_type="festivo", notes="Festivo nacional"))
            seeded += 1
    db.commit()
    return seeded


@app.post("/api/login")
def login(req: PinRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).first()
    if not user or not bcrypt.checkpw(req.pin.encode(), user.pin_hash.encode()):
        raise HTTPException(status_code=401, detail="PIN incorrecto")
    response.set_cookie("auth_token", "authenticated", max_age=60 * 60 * 24 * 30)
    return {"ok": True}


@app.get("/api/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("auth_token")
    return response


@app.post("/api/entries/manual")
def create_manual_entry(req: ManualEntryRequest, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    d = date.fromisoformat(req.date)
    entry_type = normalize_entry_type(req.entry_type)
    err = _validate_day_mark(db, d, entry_type)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if entry_type in SPECIAL_TYPES:
        t_in = time(0, 0)
        t_out = None
    else:
        if not req.time_in:
            raise HTTPException(status_code=400, detail="Se requiere hora de entrada")
        t_in = parse_time(req.time_in)
        t_out = parse_time(req.time_out) if req.time_out else None
        sch = get_schedule(db)
        err = validate_work_window(t_in, t_out, sch)
        if err:
            raise HTTPException(status_code=400, detail=err)

    entry = Entry(date=d, time_in=t_in, time_out=t_out, entry_type=entry_type)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "ok": True, "entry_type": entry.entry_type}


@app.get("/api/week")
def week_summary(request: Request, week_start: Optional[str] = None, db: Session = Depends(get_db)):
    check_auth(request)
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())

    if week_start:
        week_start = date.fromisoformat(week_start) - timedelta(days=date.fromisoformat(week_start).weekday())
    else:
        week_start = current_week_start
    week_end = week_start + timedelta(days=4)
    is_current_week = (week_start == current_week_start)

    entries = (
        db.query(Entry)
        .filter(Entry.date >= week_start, Entry.date <= week_end)
        .order_by(Entry.date, Entry.time_in)
        .all()
    )

    sch = get_schedule(db)
    days = []
    total_week = 0
    total_morning = 0.0
    total_afternoon = 0.0
    total_presencial = 0
    total_teletrabajo = 0
    total_lunch = 0
    worked_days = 0
    special_days = 0
    day_names = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes"]

    for i in range(5):
        d = week_start + timedelta(days=i)
        day_entries = [e for e in entries if e.date == d]
        day_morning = 0.0
        day_afternoon = 0.0
        day_lunch = 0.0
        has_presencial = False
        has_teletrabajo = False
        special = None
        for e in day_entries:
            m, a, l = entry_split(e.time_in, e.time_out, sch)
            day_morning += m
            day_afternoon += a
            day_lunch += l
            total_lunch += l
            if e.time_out:
                if e.entry_type == "teletrabajo":
                    total_teletrabajo += m + a
                    has_teletrabajo = True
                elif e.entry_type == "presencial":
                    total_presencial += m + a
                    has_presencial = True
            if e.entry_type in SPECIAL_TYPES:
                special = e.entry_type
        if any(e.entry_type in NON_WORK_TYPES for e in day_entries):
            special_days += 1
        if any(e.entry_type not in NON_WORK_TYPES for e in day_entries):
            worked_days += 1
        day_net = day_morning + day_afternoon
        total_morning += day_morning
        total_afternoon += day_afternoon
        total_week += day_net
        days.append({
            "date": d.isoformat(),
            "name": day_names[i],
            "day_num": d.day,
            "total": round(day_net, 2),
            "total_formatted": fmt_hours(day_net),
            "lunch_deducted": round(day_lunch, 2),
            "lunch_formatted": fmt_hours(day_lunch),
            "is_today": d == today,
            "has_presencial": has_presencial,
            "has_teletrabajo": has_teletrabajo,
            "special": special,
        })

    non_worked_days = special_days
    target = float(get_setting(db, "weekly_hours", "35"))
    daily_objective = target / 5.0
    day_discount = round(non_worked_days * daily_objective, 2)
    adjusted_target = max(0.0, round(target - day_discount, 2))
    remaining = max(0.0, round(adjusted_target - total_week, 2))
    afternoon_available_days = max(0, 5 - special_days)
    target_afternoon_week = afternoon_objective_hours(afternoon_available_days, sch)
    presencial_pct = round(total_presencial / total_week * 100) if total_week > 0 else 0
    teletrabajo_pct = round(total_teletrabajo / total_week * 100) if total_week > 0 else 0
    pct_target_telework = int(round(get_telework_pct(db)))
    pct_target_presencial = 100 - pct_target_telework
    target_tele_hours = round(adjusted_target * pct_target_telework / 100, 1)
    target_pres_hours = round(adjusted_target * pct_target_presencial / 100, 1)

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "is_current_week": is_current_week,
        "days": days,
        "total_week": round(total_week, 2),
        "total_week_formatted": fmt_hours(total_week),
        "morning_hours": round(total_morning, 2),
        "morning_formatted": fmt_hours(total_morning),
        "afternoon_hours": round(total_afternoon, 2),
        "afternoon_formatted": fmt_hours(total_afternoon),
        "lunch_deducted_week": round(total_lunch, 2),
        "lunch_deducted_formatted": fmt_hours(total_lunch),
        "presencial_hours": round(total_presencial, 2),
        "presencial_formatted": fmt_hours(total_presencial),
        "presencial_pct": presencial_pct,
        "teletrabajo_hours": round(total_teletrabajo, 2),
        "teletrabajo_formatted": fmt_hours(total_teletrabajo),
        "teletrabajo_pct": teletrabajo_pct,
        "presencial_pct_target": pct_target_presencial,
        "telework_pct_target": pct_target_telework,
        "presencial_hours_target": target_pres_hours,
        "teletrabajo_hours_target": target_tele_hours,
        "target": adjusted_target,
        "target_formatted": fmt_hours(adjusted_target),
        "target_base": round(target, 2),
        "afternoon_reduction": day_discount,
        "afternoon_reduction_formatted": fmt_hours(day_discount),
        "afternoon_target": round(target_afternoon_week, 2),
        "afternoon_target_formatted": fmt_hours(target_afternoon_week),
        "remaining": remaining,
        "remaining_formatted": fmt_hours(remaining),
        "days_worked": worked_days,
        "non_worked_days": non_worked_days,
        "afternoon_available_days": afternoon_available_days,
        "afternoon_objective_hours": round(target_afternoon_week, 2),
        "afternoon_objective_formatted": fmt_hours(target_afternoon_week),
    }


@app.get("/api/day")
def day_summary(request: Request, date_str: Optional[str] = None, db: Session = Depends(get_db)):
    check_auth(request)
    d = date.fromisoformat(date_str) if date_str else date.today()
    sch = get_schedule(db)
    entries = db.query(Entry).filter(Entry.date == d).order_by(Entry.time_in).all()
    morning_total = 0.0
    afternoon_total = 0.0
    lunch_ded = 0.0
    result = []
    for e in entries:
        m, a, l = entry_split_gross(e.time_in, e.time_out, sch)
        morning_total += m
        afternoon_total += a
        lunch_ded += l
        shift = None
        if e.time_out and e.entry_type not in SPECIAL_TYPES:
            shift = entry_shift(e.time_in, sch)
        result.append({
            "id": e.id,
            "time_in": e.time_in.strftime("%H:%M"),
            "time_out": e.time_out.strftime("%H:%M") if e.time_out else None,
            "entry_type": e.entry_type,
            "shift": shift,
            "has_morning": m > 0,
            "has_afternoon": a > 0,
            "lunch_deducted": round(l, 2),
        })
    return {
        "date": d.isoformat(),
        "entries": result,
        "total_hours": fmt_hours(morning_total + afternoon_total - lunch_ded),
        "morning_hours": fmt_hours(morning_total),
        "afternoon_hours": fmt_hours(afternoon_total),
        "lunch_deducted": fmt_hours(lunch_ded),
    }


@app.get("/api/today")
def today_summary(request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    today = date.today()
    sch = get_schedule(db)
    entries = db.query(Entry).filter(Entry.date == today).order_by(Entry.time_in).all()
    morning_total = 0.0
    afternoon_total = 0.0
    lunch_ded = 0.0
    result = []
    for e in entries:
        m, a, l = entry_split(e.time_in, e.time_out, sch)
        morning_total += m
        afternoon_total += a
        lunch_ded += l
        shift = None
        if e.time_out and e.entry_type not in SPECIAL_TYPES:
            shift = entry_shift(e.time_in, sch)
        result.append({
            "id": e.id,
            "time_in": e.time_in.strftime("%H:%M"),
            "time_out": e.time_out.strftime("%H:%M") if e.time_out else None,
            "entry_type": e.entry_type,
            "shift": shift,
            "has_morning": m > 0,
            "has_afternoon": a > 0,
            "lunch_deducted": round(l, 2),
        })
    return {
        "date": today.isoformat(),
        "entries": result,
        "total_hours": fmt_hours(morning_total + afternoon_total),
        "morning_hours": fmt_hours(morning_total),
        "afternoon_hours": fmt_hours(afternoon_total),
        "lunch_deducted": fmt_hours(lunch_ded),
    }


@app.get("/api/entries")
def get_entries(month: int, year: int, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    entries = (
        db.query(Entry)
        .filter(extract("month", Entry.date) == month, extract("year", Entry.date) == year)
        .order_by(Entry.date, Entry.time_in)
        .all()
    )
    result = []
    sch = get_schedule(db)
    for e in entries:
        net = None
        lunch = None
        shift = None
        has_morning = False
        has_afternoon = False
        if e.time_out:
            net_val, ov = entry_net(e.time_in, e.time_out, sch)
            net = round(net_val, 2)
            lunch = round(ov, 2)
            m, a, _l = entry_split(e.time_in, e.time_out, sch)
            has_morning = m > 0
            has_afternoon = a > 0
            if e.entry_type not in SPECIAL_TYPES:
                shift = entry_shift(e.time_in, sch)
        result.append({
            "id": e.id,
            "date": e.date.isoformat(),
            "weekday": ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"][e.date.weekday()],
            "time_in": e.time_in.strftime("%H:%M"),
            "time_out": e.time_out.strftime("%H:%M") if e.time_out else None,
            "entry_type": e.entry_type,
            "shift": shift,
            "has_morning": has_morning,
            "has_afternoon": has_afternoon,
            "total_hours": net,
            "lunch_deducted": lunch,
            "notes": e.notes or "",
        })
    return {"entries": result}


@app.put("/api/entries/{entry_id}")
def update_entry(entry_id: int, req: EntryUpdate, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    entry = db.query(Entry).filter(Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    new_date = date.fromisoformat(req.date)
    new_type = normalize_entry_type(req.entry_type) if req.entry_type else entry.entry_type
    if entry.entry_type == "festivo" and new_type != "festivo":
        raise HTTPException(status_code=400, detail="Un dia festivo no puede cambiar a otro tipo. Desmarquelo primero")
    err = _validate_day_mark(db, new_date, new_type, exclude_id=entry.id)
    if err:
        raise HTTPException(status_code=400, detail=err)
    entry.date = new_date
    if new_type in SPECIAL_TYPES:
        entry.time_in = time(0, 0)
        entry.time_out = None
    else:
        if not req.time_in and entry.entry_type in SPECIAL_TYPES:
            raise HTTPException(status_code=400, detail="Se requiere hora de entrada")
        if req.time_in:
            entry.time_in = parse_time(req.time_in)
        entry.time_out = parse_time(req.time_out) if req.time_out else None
        if entry.time_in and entry.time_out:
            sch = get_schedule(db)
            err = validate_work_window(entry.time_in, entry.time_out, sch)
            if err:
                raise HTTPException(status_code=400, detail=err)
    if req.entry_type:
        entry.entry_type = new_type
    entry.notes = req.notes
    db.commit()
    return {"ok": True, "entry_type": entry.entry_type}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    entry = db.query(Entry).filter(Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    db.delete(entry)
    db.commit()
    return {"ok": True}


def _sqlite_path() -> str:
    if "sqlite" not in str(engine.url):
        raise HTTPException(status_code=501, detail="Esta operacion solo esta disponible con base de datos SQLite")
    p = engine.url.database
    if not os.path.isabs(p):
        p = os.path.join(os.getcwd(), p)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="No se encontro el archivo de base de datos")
    return p


@app.post("/api/database/clear")
def clear_database(request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    deleted = db.query(Entry).filter(Entry.entry_type != "festivo").delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": deleted, "festivos_preservados": True}


@app.get("/api/database/backup")
def backup_database(request: Request):
    check_auth(request)
    src_path = _sqlite_path()
    tmp_name = src_path + ".backup_tmp"
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(tmp_name)
        src.backup(dst)
        dst.close()
        src.close()
        with open(tmp_name, "rb") as f:
            db_bytes = f.read()
        os.remove(tmp_name)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise HTTPException(status_code=500, detail="No se pudo generar la copia de seguridad")
    if len(db_bytes) == 0:
        raise HTTPException(status_code=500, detail="No se pudo generar la copia de seguridad")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(db_bytes)
    fname = "backup_horarios_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db.gz"
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/database/restore")
async def restore_database(request: Request, file: UploadFile = File(...)):
    check_auth(request)
    src_path = _sqlite_path()
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo es demasiado grande")
    raw = data
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="El archivo comprimido es invalido")
    if not raw.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=400, detail="El archivo no es un respaldo valido de la base de datos")
    tmp = src_path + ".restore_tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        conn = sqlite3.connect(tmp)
        corrupt = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if not corrupt or corrupt[0] != "ok":
            raise ValueError("dano")
    except Exception:
        os.remove(tmp) if os.path.exists(tmp) else None
        raise HTTPException(status_code=400, detail="El archivo de respaldo esta danado")
    engine.dispose()
    os.replace(tmp, src_path)
    init_db()
    return {"ok": True}


@app.get("/api/report/monthly")
def monthly_report(month: int, year: int, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    entries = (
        db.query(Entry)
        .filter(extract("month", Entry.date) == month, extract("year", Entry.date) == year)
        .order_by(Entry.date, Entry.time_in)
        .all()
    )

    by_day = {}
    sch = get_schedule(db)
    for e in entries:
        d = e.date.isoformat()
        if d not in by_day:
            by_day[d] = {"date": d, "weekday": ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"][e.date.weekday()], "total": 0, "lunch": 0, "entries": []}
        if e.time_out:
            net, ov = entry_net(e.time_in, e.time_out, sch)
            by_day[d]["total"] += net
            by_day[d]["lunch"] += ov
        by_day[d]["entries"].append({
            "id": e.id,
            "time_in": e.time_in.strftime("%H:%M"),
            "time_out": e.time_out.strftime("%H:%M") if e.time_out else None,
            "entry_type": e.entry_type,
        })

    by_week = {}
    total_month = 0
    total_presencial = 0
    total_teletrabajo = 0
    for d_key in sorted(by_day.keys()):
        d_obj = date.fromisoformat(d_key)
        wk = get_week_number(d_obj)
        wk_key = f"{year}-W{wk:02d}"
        if wk_key not in by_week:
            by_week[wk_key] = {"week": wk_key, "days": [], "total": 0}
        by_week[wk_key]["days"].append(by_day[d_key])
        by_week[wk_key]["total"] += by_day[d_key]["total"]
        total_month += by_day[d_key]["total"]

    total_presencial = 0
    total_teletrabajo = 0
    for day in by_day.values():
        for e in day["entries"]:
            h = _entry_hours(e, sch)
            if (e["entry_type"] or "presencial") == "teletrabajo":
                total_teletrabajo += h
            else:
                total_presencial += h

    festivo_days = vacaciones_days = ap_days = otro_days = 0
    for day in by_day.values():
        ets = {e["entry_type"] for e in day["entries"]}
        if "festivo" in ets:
            festivo_days += 1
        if "vacaciones" in ets:
            vacaciones_days += 1
        if "ap" in ets:
            ap_days += 1
        if "otro" in ets:
            otro_days += 1

    return {
        "month": month,
        "year": year,
        "weeks": list(by_week.values()),
        "total_month_hours": round(total_month, 2),
        "total_month_formatted": fmt_hours(total_month),
        "presencial_hours": round(total_presencial, 2),
        "presencial_formatted": fmt_hours(total_presencial),
        "teletrabajo_hours": round(total_teletrabajo, 2),
        "teletrabajo_formatted": fmt_hours(total_teletrabajo),
        "festivo_days": festivo_days,
        "vacaciones_days": vacaciones_days,
        "ap_days": ap_days,
        "otro_days": otro_days,
    }


@app.get("/api/export/csv")
def export_csv(month: int, year: int, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    entries = (
        db.query(Entry)
        .filter(extract("month", Entry.date) == month, extract("year", Entry.date) == year)
        .order_by(Entry.date, Entry.time_in)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha", "Dia", "Entrada", "Salida", "Total Horas", "Turno", "Tipo", "Notas"])

    sch = get_schedule(db)
    for e in entries:
        total = ""
        turno = ""
        if e.time_out:
            net, _ov = entry_net(e.time_in, e.time_out, sch)
            total = f"{net:.2f}"
            if e.entry_type not in SPECIAL_TYPES:
                turno = "Manana" if entry_shift(e.time_in, sch) == "manana" else "Tarde"
        weekday = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"][e.date.weekday()]
        tipo = TYPE_LABELS.get(e.entry_type, "Presencial")
        writer.writerow([
            e.date.strftime("%d/%m/%Y"),
            weekday,
            e.time_in.strftime("%H:%M") if e.time_in else "",
            e.time_out.strftime("%H:%M") if e.time_out else "",
            total,
            turno,
            tipo,
            e.notes or "",
        ])

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=horario_{year}_{month:02d}.csv"},
    )


@app.get("/api/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    telework_pct = get_telework_pct(db)
    schedule = {}
    for key, default in SCHEDULE_DEFAULTS.items():
        schedule[key] = get_setting(db, key, default)
    return {
        "weekly_hours": float(get_setting(db, "weekly_hours", "35")),
        "telework_pct": telework_pct,
        "presencial_pct": 100 - telework_pct,
        "default_telework_days": parse_telework_days(get_setting(db, "default_telework_days", "")),
        "schedule": schedule,
    }


@app.post("/api/settings")
def update_settings(req: SettingsUpdate, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    for key in SCHEDULE_DEFAULTS:
        val = getattr(req, key, None)
        if val is not None:
            try:
                parse_time(val)
            except Exception:
                raise HTTPException(status_code=400, detail="Formato de hora incorrecto (use HH:MM)")
    fields = {
        "weekly_hours": req.weekly_hours,
        "telework_pct": req.telework_pct,
        "default_telework_days": req.default_telework_days,
    }
    for key, val in fields.items():
        if val is None:
            continue
        s = db.query(Setting).filter(Setting.key == key).first()
        if s:
            s.value = str(val)
        else:
            db.add(Setting(key=key, value=str(val)))
    for key in SCHEDULE_DEFAULTS:
        val = getattr(req, key, None)
        if val is None:
            continue
        s = db.query(Setting).filter(Setting.key == key).first()
        if s:
            s.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True}


@app.post("/api/change-pin")
def change_pin(req: PinRequest, request: Request, db: Session = Depends(get_db)):
    check_auth(request)
    user = db.query(User).first()
    if not user:
        raise HTTPException(status_code=400, detail="No hay usuario")
    new_hash = bcrypt.hashpw(req.pin.encode(), bcrypt.gensalt()).decode()
    user.pin_hash = new_hash
    db.commit()
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    return templates.TemplateResponse("calendar.html", {"request": request})


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    return templates.TemplateResponse("history.html", {"request": request})


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    return templates.TemplateResponse("reports.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("APP_RELOAD", "0") == "1",
    )
