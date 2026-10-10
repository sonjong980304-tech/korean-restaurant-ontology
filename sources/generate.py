"""원천 시스템 4개(POS·MES·SCM·ERP)의 가상 데이터를 각 시스템 고유 형식으로 만든다.

일부러 시스템마다 다르게 만든 것(온톨로지 파이프라인이 풀어야 할 숙제):
  - 코드:   POS 메뉴코드 P-101 / MES 레시피코드 R-JJ-KIMCHI / SCM 품목코드 VEG-ONION-1KG
  - 이름:   MES '양파' / SCM '양파(국산) 1kg망'
  - 단위:   MES g·ml / SCM kg·L
  - 값 표현: POS 성별 M·F, 출생연도 / 동행유형 코드 SOLO·FAMILY…
  - 시각:   POS '2026-07-01 12:03:00' / MES '20260701T120300' / ERP '2026/07/01'

실행: .venv/bin/python sources/generate.py  →  data/raw/{pos,mes,scm,erp}/*.csv
"""
import csv
import math
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw"
SEED = 42

MASTER = yaml.safe_load((ROOT / "sources" / "master.yaml").read_text(encoding="utf-8"))
PROFILE = yaml.safe_load((ROOT / "semantic" / "profile.yaml").read_text(encoding="utf-8"))

# ══════════════════ 심어 둔 패턴(PLANTED) — 나중에 분석으로 다시 찾게 될 것들 ══════════════════
# 1) 동행유형 비율: (평일/주말, 점심/저녁)마다 다르다. 금요일 저녁은 회식이 많다.
PARTY_MIX = {
    ("평일", "점심"): {"혼밥": 25, "친구": 30, "비즈니스": 20, "회식": 5, "연인": 5, "가족": 15},
    ("평일", "저녁"): {"혼밥": 12, "친구": 30, "비즈니스": 6, "회식": 12, "연인": 18, "가족": 22},
    ("금요일", "저녁"): {"혼밥": 6, "친구": 30, "비즈니스": 5, "회식": 22, "연인": 20, "가족": 17},
    ("주말", "점심"): {"혼밥": 10, "친구": 20, "비즈니스": 5, "회식": 0, "연인": 20, "가족": 45},
    ("주말", "저녁"): {"혼밥": 5, "친구": 27, "비즈니스": 3, "회식": 3, "연인": 22, "가족": 40},
}
PARTY_SIZE = {"혼밥": (1, 1), "연인": (2, 2), "친구": (2, 4), "가족": (3, 5), "회식": (5, 7), "비즈니스": (2, 4)}
# 2) 체류시간(분, 저녁 기준). 가족은 길고 회식은 가장 길다. 점심은 회식 외 0.6배.
DWELL = {"혼밥": 25, "연인": 60, "친구": 55, "가족": 70, "회식": 110, "비즈니스": 50}
# 3) 대기 인내심(분, 지수분포 평균). 점심 손님이 더 조급하다 → 점심 대기 이탈률이 높다.
PATIENCE = {"점심": 10, "저녁": 18, "예약": 60}
# 4) 메뉴 취향: 동행유형마다 선호 메뉴가 다르다.
TASTE_MEAL = {
    "점심": {"김치찌개정식": 8, "제육정식": 7, "김치찌개": 6, "된장찌개": 6, "순두부찌개": 5, "부대찌개": 3, "갈비탕": 3,
             "제육덮밥": 6, "비빔밥": 4, "돌솥비빔밥": 3, "고등어구이": 3, "물냉면": 4, "비빔국수": 3},
    "가족": {"불고기": 6, "갈비탕": 4, "비빔밥": 4, "돌솥비빔밥": 4, "된장찌개": 4, "김치찌개": 3, "LA갈비": 3,
             "물냉면": 3, "고등어구이": 3},
    "연인": {"불고기": 4, "순두부찌개": 4, "돌솥비빔밥": 4, "제육볶음": 4, "물냉면": 3, "부대찌개": 3},
    "저녁": {"김치찌개": 5, "된장찌개": 4, "부대찌개": 4, "갈비탕": 3, "제육볶음": 4, "불고기": 3, "고등어구이": 3,
             "비빔밥": 3, "돌솥비빔밥": 3, "물냉면": 2},
}
TASTE_SHARE = {"보쌈": 6, "해물파전": 5, "제육볶음": 5, "김치전": 4, "계란말이": 3, "LA갈비": 3, "부대찌개": 4}
# 5) 술: 1인당 평균 병 수. 회식이 압도적이다.
DRINKS_PER_PERSON = {"회식": 1.8, "친구": 1.0, "연인": 0.6, "비즈니스": 0.8, "가족": 0.3, "혼밥": 0.2}
# 6) 계절: 냉면·국수는 7~8월에 잘 팔리고 9월에 꺾인다. 갈비탕·순두부는 반대.
SEASON = {"물냉면": {7: 1.6, 8: 1.8, 9: 0.6}, "비빔국수": {7: 1.5, 8: 1.6, 9: 0.7},
          "갈비탕": {7: 0.8, 8: 0.7, 9: 1.2}, "순두부찌개": {7: 0.8, 8: 0.8, 9: 1.2}}
# 7) 단가 이벤트(SCM): 날짜 이후 단가 배수.
PRICE_EVENTS = [("VEG-ONION-1KG", date(2026, 9, 1), 1.40),     # 양파 +40%
                ("MT-PORK-SHLD", date(2026, 8, 15), 1.15),     # 돼지앞다리 +15%
                ("VEG-NAPA", date(2026, 8, 10), 1.60)]         # 배추 +60%(폭염)
# 8) 이탈 단골: 충성도 상위 10% 회원 중 30%가 8/25 이후 발길을 끊는다.
CHURN_DATE = date(2026, 8, 25)
# 9) 주방 인원 부족 → 조리시간 증가(ERP 근태 → MES 조리시간). 주방장 휴무(화) 점심, 주방보조 휴무(목) 저녁이 느리다.
# 10) 홀 아르바이트가 적은 날(평일) → 테이블 정리가 늦어 다음 손님 착석이 늦다.
# 11) 과다 사용(로스): 레시피보다 평균 3% 더 쓰고, 저녁 제육볶음·불고기는 8% 더 쓴다.
# 12) 폐기: 유통기한 짧은 채소(상추·콩나물)는 폐기율이 높다.
# ══════════════════════════════════════════════════════════════════════════════════════

STATION_CODE = {"찌개": "STEW", "구이": "GRILL", "찬·면": "COLD", "음료": "BAR"}
PARTY_CODE = {"혼밥": "SOLO", "연인": "COUPLE", "친구": "FRIENDS", "가족": "FAMILY", "회식": "TEAM", "비즈니스": "BIZ"}
BASE_COOK_MIN = {"찌개": 8, "구이": 12, "찬·면": 6, "음료": 1}
WEEKDAY_KO = "월화수목금토일"
ALCOHOL = [("소주", 50), ("맥주", 35), ("막걸리", 15)]


def pos_dt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def mes_dt(d):
    return d.strftime("%Y%m%dT%H%M%S")


def erp_d(d):
    return d.strftime("%Y/%m/%d")


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def weighted(rng, table):
    names = list(table)
    return rng.choices(names, weights=[table[n] for n in names])[0]


class Store:
    """3개월 영업을 하루씩 돌리며 네 시스템의 기록을 쌓는다."""

    def __init__(self, seed=SEED):
        self.rng = random.Random(seed)
        self.menus = {m["name"]: m for m in MASTER["menus"]}
        self.ingredients = {i["mes"]: i for i in MASTER["ingredients"]}
        self.start, self.end = (date.fromisoformat(d) for d in PROFILE["restaurant"]["period"])
        self.rows = defaultdict(list)            # (시스템, 표) → 행 목록
        self.daily_usage = defaultdict(lambda: defaultdict(float))  # 날짜 → MES 재료명 → 기본단위 사용량
        self.seq = defaultdict(int)
        self._build_tables()
        self._build_members()

    def next_id(self, kind):
        self.seq[kind] += 1
        return self.seq[kind]

    # ── 기준정보 ──
    def _build_tables(self):
        self.tables = []
        n = 0
        for spec in PROFILE["tables"]:
            for _ in range(spec["count"]):
                n += 1
                self.tables.append({"no": f"T{n:02d}", "zone": spec["zone"], "cap": spec["capacity"]})

    def _build_members(self):
        rng = self.rng
        self.members = []
        for i in range(PROFILE["restaurant"]["members"]):
            by = rng.randint(1950, 2005)
            self.members.append({
                "no": f"M{100001 + i}",
                "sex": "" if rng.random() < 0.1 else rng.choice("MF"),
                "birth_year": "" if rng.random() < 0.1 else by,
                "age": 2026 - by,
                "seg": "직장인" if rng.random() < 0.55 else "주민",
                "loyalty": min(rng.paretovariate(1.3), 30),
                "join": date(2023, 1, 1) + timedelta(days=rng.randint(0, 1276)),
            })
        top = sorted(self.members, key=lambda m: -m["loyalty"])[: len(self.members) // 10]
        for m in top:
            m["churn"] = rng.random() < 0.3
        self._member_weights = {}

    def _affinity(self, m, pt, weekday_lunch):
        age = m["age"]
        if pt in ("회식", "비즈니스"):
            a = 3 if m["seg"] == "직장인" else 0.2
        elif pt == "가족":
            a = 3 if m["seg"] == "주민" and 30 <= age < 60 else 0.3
        elif pt == "연인":
            a = 3 if age < 40 else 0.3
        elif pt == "혼밥":
            a = 2 if m["seg"] == "직장인" else 0.7
        else:
            a = 1
        if weekday_lunch and m["seg"] == "직장인":
            a *= 2
        return a * m["loyalty"]

    def pick_member(self, pt, weekday_lunch, d):
        key = (pt, weekday_lunch, d >= CHURN_DATE)
        if key not in self._member_weights:
            self._member_weights[key] = [
                0 if (key[2] and m.get("churn")) else self._affinity(m, pt, weekday_lunch) for m in self.members
            ]
        return self.rng.choices(self.members, weights=self._member_weights[key])[0]

    # ── 하루 영업 ──
    def day_kind(self, d):
        wd = d.weekday()
        if wd >= 5:
            return "주말"
        return "금요일" if wd == 4 else "평일"

    def staff_on(self, d):
        """ERP 근태를 만들고, 그날 주방·홀 인원을 돌려준다(패턴 9·10)."""
        rng, wd = self.rng, WEEKDAY_KO[d.weekday()]
        on = [e for e in MASTER["employees"] if e["employment"] == "정규" and e.get("day_off") != wd]
        albas = [e for e in MASTER["employees"] if e["employment"] == "아르바이트"]
        n_alba = {"평일": 2, "금요일": 3, "주말": 4}[self.day_kind(d)]
        on += rng.sample(albas, n_alba)
        shifts = {s["id"]: s for s in PROFILE["shifts"]}
        for e in on:
            for start, end in shifts[e["shift"]]["blocks"]:
                cin = datetime.combine(d, time.fromisoformat(start)) + timedelta(minutes=rng.randint(-8, 3))
                cout = datetime.combine(d, time.fromisoformat(end)) + timedelta(minutes=rng.randint(-3, 12))
                self.rows["erp", "attendance"].append(
                    [f"AT{self.next_id('att'):06d}", e["id"], erp_d(d), e["shift"], cin.strftime("%H:%M"), cout.strftime("%H:%M")])
        ids = {e["id"] for e in on}
        return {
            "kitchen_lunch": len(ids & {"E02", "E03"}),
            "kitchen_dinner": len(ids & {"E04"}) + 1,   # 사장이 저녁 주방을 거든다
            "albas": n_alba,
        }

    def simulate_day(self, d):
        rng = self.rng
        kind = self.day_kind(d)
        staff = self.staff_on(d)
        lo, hi = {"평일": (120, 145), "금요일": (155, 180), "주말": (145, 175)}[kind]
        n = rng.randint(lo, hi)
        lunch_share = 0.45 if kind == "주말" else 0.55
        arrivals = []
        for _ in range(n):
            if rng.random() < lunch_share:
                slot, center, sd, lo_t, hi_t = "점심", 12 * 60 + 20, 50, 11 * 60, 14 * 60 + 20
            else:
                slot, center, sd, lo_t, hi_t = "저녁", 19 * 60, 75, 17 * 60, 21 * 60
            m = min(max(rng.gauss(center, sd), lo_t), hi_t)
            arrivals.append((datetime.combine(d, time()) + timedelta(minutes=m), slot))
        arrivals.sort()

        free_at = {t["no"]: datetime.combine(d, time(10, 50)) for t in self.tables}
        bus_min = 2 if staff["albas"] >= 3 else 4                          # 패턴 10
        kitchen_load = defaultdict(int)                                    # 15분 구간별 조리지시 수
        close_no = f"CL-{d:%Y%m%d}"
        seated_people = 0
        pay_count = sales = 0

        for arrive, slot in arrivals:
            mix_key = ("금요일" if kind == "금요일" and slot == "저녁" else ("주말" if kind == "주말" else "평일"), slot)
            if mix_key[0] == "금요일" and slot == "점심":
                mix_key = ("평일", "점심")
            pt = weighted(rng, PARTY_MIX[mix_key])
            size = rng.randint(*PARTY_SIZE[pt])
            child = pt == "가족" and rng.random() < 0.6
            reserved = rng.random() < {"회식": 0.6, "가족": 0.3}.get(pt, 0.05)
            member = None
            if rng.random() < 1 - PROFILE["restaurant"]["non_member_ratio"]:
                member = self.pick_member(pt, kind != "주말" and slot == "점심", d)

            # 테이블 배정: 수용 가능 + 큰 룸에 혼밥 금지. 가족(아이)·회식은 룸 우선.
            fits = [t for t in self.tables if t["cap"] >= size and (t["cap"] <= 4 or size >= 3)]
            prefer_room = child or pt == "회식"
            order_key = lambda t: ((t["zone"] != "Z-ROOM") if prefer_room else (t["zone"] == "Z-ROOM"), t["cap"])
            ready = sorted([t for t in fits if free_at[t["no"]] <= arrive], key=order_key)
            visit_no = f"V{d:%y%m%d}{self.next_id(('v', d)):04d}"
            wait_row = None
            if ready:
                table, seat = ready[0], arrive
            else:
                table = min(fits, key=lambda t: (free_at[t["no"]], order_key(t)))
                seat = free_at[table["no"]]
                wait = (seat - arrive).total_seconds() / 60
                patience = rng.expovariate(1 / PATIENCE["예약" if reserved else slot])
                quoted = max(5, round(wait * rng.uniform(0.7, 1.2) / 5) * 5)
                wait_no = f"W{d:%y%m%d}{self.next_id(('w', d)):04d}"
                if wait > patience:                                        # 패턴 3: 못 기다리고 떠남
                    self.rows["pos", "waiting"].append([wait_no, visit_no, pos_dt(arrive), "", quoted, "LEFT"])
                    self.rows["pos", "visit"].append([visit_no, member["no"] if member else "", pos_dt(arrive), "", "",
                                                      PARTY_CODE[pt], size, "Y" if child else "N",
                                                      "Y" if reserved else "N", ""])
                    continue
                wait_row = [wait_no, visit_no, pos_dt(arrive), pos_dt(seat), quoted, "SEATED"]

            dwell = DWELL[pt] * (0.6 if slot == "점심" and pt != "회식" else 1) * rng.lognormvariate(0, 0.2)
            leave = seat + timedelta(minutes=dwell)
            free_at[table["no"]] = leave + timedelta(minutes=bus_min)
            seated_people += size
            if wait_row:
                self.rows["pos", "waiting"].append(wait_row)
            self.rows["pos", "visit"].append([visit_no, member["no"] if member else "", pos_dt(arrive), pos_dt(seat),
                                              pos_dt(leave), PARTY_CODE[pt], size, "Y" if child else "N",
                                              "Y" if reserved else "N", table["no"]])

            amount = 0
            for when, lines in self.order_lines(pt, size, child, slot, d, seat, dwell):
                order_no = f"O{d:%y%m%d}{self.next_id(('o', d)):05d}"
                self.rows["pos", "order"].append([order_no, visit_no, pos_dt(when)])
                for name, qty in lines.items():
                    menu = self.menus[name]
                    self.rows["pos", "order_line"].append([order_no, menu["pos"], qty, menu["price"]])
                    amount += menu["price"] * qty
                    self.cook(order_no, menu, qty, when, slot, staff, kitchen_load, d)
            method = weighted(rng, {"CARD": 70, "EASY": 22, "CASH": 8})
            self.rows["pos", "payment"].append(
                [f"PAY{d:%y%m%d}{self.next_id(('p', d)):04d}", visit_no, pos_dt(leave), amount, method, close_no])
            pay_count += 1
            sales += amount

        self.rows["pos", "daily_close"].append([close_no, d.isoformat(), sales, pay_count])
        self.banchan_and_waste(d, seated_people)

    def order_lines(self, pt, size, child, slot, d, seat, dwell):
        """방문 한 번의 주문들: [(주문시각, {메뉴명: 수량})]."""
        rng = self.rng
        first = defaultdict(int)
        lunch_ok = lambda name: slot == "점심" or not self.menus[name].get("lunch_only")
        season = lambda name: SEASON.get(name, {}).get(d.month, 1)

        if slot == "저녁" and pt in ("회식", "친구") and size >= 2:     # 안주를 나눠 먹는다
            for _ in range(math.ceil(size / 2)):
                first[weighted(rng, TASTE_SHARE)] += 1
            meal_people = sum(rng.random() < 0.3 for _ in range(size))
        else:
            meal_people = size
        taste = TASTE_MEAL["점심" if slot == "점심" else ("가족" if pt == "가족" else "연인" if pt == "연인" else "저녁")]
        taste = {k: v * season(k) for k, v in taste.items() if lunch_ok(k)}
        for _ in range(meal_people):
            first[weighted(rng, taste)] += 1
        if child and rng.random() < 0.5:
            first["계란말이"] += 1
        kids = rng.randint(1, 2) if child else 0
        if kids:
            first["음료"] += kids
        drinks = DRINKS_PER_PERSON[pt] * (size - kids) * (0.15 if slot == "점심" else 1)
        n_drinks = int(drinks) + (rng.random() < drinks - int(drinks))
        later = defaultdict(int)
        for i in range(n_drinks):
            name = "음료" if slot == "점심" else weighted(rng, dict(ALCOHOL))
            (later if i >= 2 and slot == "저녁" else first)[name] += 1    # 술은 일부를 나중에 추가 주문
        orders = [(seat + timedelta(minutes=rng.uniform(1, 4)), dict(first))]
        if later:
            orders.append((seat + timedelta(minutes=dwell * rng.uniform(0.4, 0.7)), dict(later)))
        return orders

    def cook(self, order_no, menu, qty, ordered, slot, staff, load, d):
        """MES: 조리지시 1건 + 레시피만큼 재료 소진(패턴 9·11)."""
        rng = self.rng
        bucket = ordered.replace(minute=ordered.minute // 15 * 15, second=0, microsecond=0)
        load[bucket] += qty
        cooks = staff["kitchen_lunch"] if slot == "점심" else staff["kitchen_dinner"]
        busy = 1 + 0.02 * max(0, load[bucket] - 15)
        short = 1 + 0.35 * max(0, 2 - cooks)
        base = BASE_COOK_MIN[menu["station"]]
        start = ordered + timedelta(minutes=rng.uniform(0.5, 3) * busy * short)
        done = start + timedelta(minutes=base * busy * short * rng.lognormvariate(0, 0.15) * (1 + 0.3 * (qty - 1)))
        ticket = f"K{self.next_id('k'):07d}"
        self.rows["mes", "kds_ticket"].append(
            [ticket, order_no, menu["kitchen"], STATION_CODE[menu["station"]], qty, mes_dt(start), mes_dt(done)])
        over = 1.08 if slot == "저녁" and menu["name"] in ("제육볶음", "불고기") else 1.03
        for mat, per in menu["recipe"].items():
            used = per * qty * (1 if self.ingredients[mat]["uom"] == "ea" else rng.gauss(over, 0.04))
            used = round(used, 1)
            self.rows["mes", "consumption"].append(
                [f"C{self.next_id('c'):08d}", ticket, mat, used, "USE", mes_dt(start), f"MC-{d:%Y%m%d}"])
            self.daily_usage[d][mat] += used

    def banchan_and_waste(self, d, people):
        rng = self.rng
        close = f"MC-{d:%Y%m%d}"
        at = datetime.combine(d, time(10, 30))
        for mat, per in MASTER["banchan_per_person"].items():
            used = round(per * people * rng.gauss(1.0, 0.05), 1)
            self.rows["mes", "consumption"].append([f"C{self.next_id('c'):08d}", "", mat, used, "USE", mes_dt(at), close])
            self.daily_usage[d][mat] += used
        end = datetime.combine(d, time(22, 30))
        for mat, used in list(self.daily_usage[d].items()):
            ing = self.ingredients[mat]
            if ing["shelf"] > 7:
                continue
            rate = rng.uniform(0.06, 0.10) if ing["shelf"] <= 4 else rng.uniform(0.01, 0.04)   # 패턴 12
            waste = round(used * rate, 1)
            self.rows["mes", "consumption"].append([f"C{self.next_id('c'):08d}", "", mat, waste, "WASTE", mes_dt(end), close])
            self.daily_usage[d][mat] += waste

    # ── SCM: 재고·발주(수요가 정해진 뒤 재고 정책을 돌린다) ──
    def price(self, item, supplier, d):
        p = item["price"] * MASTER["suppliers"][supplier].get("price_factor", 1)
        for code, since, factor in PRICE_EVENTS:
            if code == item["scm"] and d >= since:
                p *= factor
        return round(p)

    def run_scm(self):
        rng = self.rng
        conv = lambda ing: 1 if ing["uom"] == "ea" else 1000
        days = [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]
        first_week = [d for d in days[:8] if d in self.daily_usage]
        avg0 = {m: sum(self.daily_usage[d][m] for d in first_week) / len(first_week) for m in self.ingredients}
        stock = {m: avg0[m] * 10 for m in self.ingredients}
        self.cost_basis = {}
        open_at = datetime.combine(self.start - timedelta(days=1), time(23, 0))
        for m, ing in self.ingredients.items():
            self.rows["scm", "stock_adjust"].append(
                [f"ADJ{self.next_id('adj'):05d}", ing["scm"], round(stock[m] / conv(ing), 3), pos_dt(open_at), "OPENING"])
        pending = []                                      # (도착일, po_no, 품목, 수량(SCM단위), 단가)
        self.min_stock = {m: math.inf for m in self.ingredients}
        for d in days:
            for arr in [p for p in pending if p[0] == d]:
                pending.remove(arr)
                _, po_no, ing, qty, price = arr
                stock[ing["mes"]] += qty * conv(ing)
                self.rows["scm", "receipt"].append(
                    [f"RC{self.next_id('rc'):06d}", po_no, ing["scm"], qty, price, pos_dt(datetime.combine(d, time(9, 0)))])
            for m, used in self.daily_usage.get(d, {}).items():
                stock[m] -= used
                self.min_stock[m] = min(self.min_stock[m], stock[m])
            recent = [x for x in days if d - timedelta(days=7) < x <= d and x in self.daily_usage]
            by_supplier = defaultdict(list)
            for m, ing in self.ingredients.items():
                avg = sum(self.daily_usage[x][m] for x in recent) / max(len(recent), 1) or avg0[m]
                lead = MASTER["suppliers"][ing["supplier"]]["lead"]
                on_order = sum(p[3] * conv(p[2]) for p in pending if p[2] is ing)
                if stock[m] + on_order < avg * (lead + 3):
                    qty = math.ceil((avg * (lead + 5) - stock[m] - on_order) / conv(ing))
                    by_supplier[ing["supplier"]].append((ing, qty))
            for sup, lines in by_supplier.items():
                po_no = f"PO{d:%y%m%d}-{sup[4:]}"
                arrive = d + timedelta(days=MASTER["suppliers"][sup]["lead"])
                if arrive.weekday() == 0:                 # 월요일 휴무 → 화요일 입고
                    arrive += timedelta(days=1)
                status = "RECEIVED" if arrive <= self.end else "CONFIRMED"
                self.rows["scm", "purchase_order"].append([po_no, sup, pos_dt(datetime.combine(d, time(22, 40))),
                                                           arrive.isoformat(), status])
                for ing, qty in lines:
                    price = self.price(ing, sup, d)
                    self.rows["scm", "po_line"].append([po_no, ing["scm"], qty, price])
                    if arrive <= self.end:
                        pending.append((arrive, po_no, ing, qty, price))
            if (d + timedelta(days=1)).day == 1:          # 월말 실사: 장부보다 조금 적다
                for m, ing in self.ingredients.items():
                    if ing["storage"] != "상온" and stock[m] > 0:
                        diff = -stock[m] * rng.uniform(0.002, 0.01)
                        stock[m] += diff
                        self.rows["scm", "stock_adjust"].append(
                            [f"ADJ{self.next_id('adj'):05d}", ing["scm"], round(diff / conv(ing), 3),
                             pos_dt(datetime.combine(d, time(23, 0))), "COUNT"])

    # ── ERP: 전표 ──
    def run_erp(self):
        rng = self.rng
        lines = self.rows["erp", "journal_line"]
        journal = self.rows["erp", "journal"]

        def post(je_no, d, desc, src, src_type, debit, credit):
            journal.append([je_no, erp_d(d), desc, src, src_type])
            for acct, amt in debit:
                lines.append([je_no, acct, round(amt), 0])
            for acct, amt in credit:
                lines.append([je_no, acct, 0, round(amt)])

        pays = defaultdict(lambda: defaultdict(int))
        for p in self.rows["pos", "payment"]:
            pays[p[5]][p[4]] += p[3]
        for close_no, biz, total, _ in self.rows["pos", "daily_close"]:
            by = pays[close_no]
            post(f"JE-S{biz.replace('-', '')}", date.fromisoformat(biz), "일 매출 마감", close_no, "SALES",
                 [("101", by["CASH"]), ("108", by["CARD"] + by["EASY"])], [("401", total)])

        po_lines = defaultdict(list)
        for po_no, item, qty, price in self.rows["scm", "po_line"]:
            po_lines[po_no].append(qty * price)
        received = {}
        for _, po_no, _, _, _, at in self.rows["scm", "receipt"]:
            received[po_no] = at[:10]
        for po_no, d in received.items():
            amt = sum(po_lines[po_no])
            post(f"JE-P{po_no[2:]}", date.fromisoformat(d), "식재료 매입", po_no, "PURCHASE",
                 [("146", amt)], [("251", amt)])

        # 원가: 그날 소진·폐기량 × 그날 주거래처 단가
        for d, usage in sorted(self.daily_usage.items()):
            cost = 0
            for m, used in usage.items():
                ing = self.ingredients[m]
                conv = 1 if ing["uom"] == "ea" else 1000
                cost += used / conv * self.price(ing, ing["supplier"], d)
            post(f"JE-C{d:%Y%m%d}", d, "식재료 사용(원가) 마감", f"MC-{d:%Y%m%d}", "COGS", [("451", cost)], [("146", cost)])

        wage = {e["id"]: e for e in MASTER["employees"]}
        hours = defaultdict(float)
        for _, emp, d, _, cin, cout in self.rows["erp", "attendance"]:
            if wage[emp]["employment"] == "아르바이트":
                h = (datetime.strptime(cout, "%H:%M") - datetime.strptime(cin, "%H:%M")).seconds / 3600
                hours[d[:7]] += h * wage[emp]["wage"]
        for month in (7, 8, 9):
            period = f"PR-2026{month:02d}"
            first = date(2026, month, 1)
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            self.rows["erp", "payroll"].append([period, erp_d(first), erp_d(last)])
            salary = sum(e.get("salary", 0) for e in MASTER["employees"])
            alba = hours[f"2026/{month:02d}"]
            post(f"JE-L2026{month:02d}", last, "급여 마감", period, "PAYROLL",
                 [("801", salary), ("805", alba)], [("262", salary + alba)])
            rent = MASTER["fixed_costs"]["rent"]
            util = MASTER["fixed_costs"]["utilities_base"] * (1.15 if month == 8 else 1) * rng.uniform(0.95, 1.05)
            post(f"JE-F2026{month:02d}R", last, "임차료", "", "FIXED", [("819", rent)], [("101", rent)])
            post(f"JE-F2026{month:02d}U", last, "수도광열비", "", "FIXED", [("815", util)], [("101", util)])

    # ── 마스터 테이블 + 저장 ──
    def write(self):
        cat_code = {name: f"C{i + 1}" for i, name in enumerate(PROFILE["menu_categories"])}
        style = {"입식": "STAND", "좌식": "FLOOR"}
        R = self.rows
        R["pos", "member"] = [[m["no"], m["sex"], m["birth_year"], m["join"].isoformat()] for m in self.members]
        R["pos", "zone"] = [[z["id"], z["name"], style[z["seating"]]] for z in PROFILE["zones"]]
        R["pos", "table"] = [[t["no"], t["zone"], t["cap"]] for t in self.tables]
        R["pos", "menu_category"] = [[c, n] for n, c in cat_code.items()]
        R["pos", "menu"] = [[m["pos"], m["name"], m["price"], cat_code[m["cat"]], m["kitchen"], "N"] for m in MASTER["menus"]]
        R["mes", "recipe"] = [[m["kitchen"], m["name"], STATION_CODE[m["station"]]] for m in MASTER["menus"]]
        R["mes", "recipe_line"] = [[m["kitchen"], mat, q] for m in MASTER["menus"] for mat, q in m["recipe"].items()]
        R["mes", "daily_consumption"] = [[f"MC-{d:%Y%m%d}", d.isoformat()] for d in sorted(self.daily_usage)]
        n_days = len(self.daily_usage)
        R["scm", "item_master"] = []
        for m, ing in self.ingredients.items():
            conv = 1 if ing["uom"] == "ea" else 1000
            avg = sum(u[m] for u in self.daily_usage.values()) / n_days / conv
            R["scm", "item_master"].append([ing["scm"], ing["scm_name"], ing["uom"].upper(), ing["storage"],
                                            ing["shelf"], round(avg * 2, 2), ing["supplier"]])
        R["scm", "vendor"] = [[s["id"], s["name"], s["category"], MASTER["suppliers"][s["id"]]["lead"],
                              MASTER["suppliers"][s["id"]]["terms"]] for s in PROFILE["suppliers"]]
        R["scm", "vendor_price"] = []
        for ing in MASTER["ingredients"]:
            sups = [ing["supplier"]] + (["SUP-MEAT-B"] if ing["supplier"] == "SUP-MEAT-A" else [])
            for sup in sups:
                R["scm", "vendor_price"].append([sup, ing["scm"], self.price(ing, sup, self.end), 1])
        R["erp", "hr_employee"] = [[e["id"], e["name"], e["role"], e["employment"], e.get("salary", ""), e.get("wage", "")]
                                   for e in MASTER["employees"]]
        R["erp", "shift_master"] = [[s["id"], s["name"], "|".join(f"{a}-{b}" for a, b in s["blocks"])]
                                    for s in PROFILE["shifts"]]
        R["erp", "chart_of_accounts"] = [[a["code"], a["name"], a["type"]] for a in MASTER["accounts"]]

        headers = {
            ("pos", "member"): ["member_no", "sex", "birth_year", "join_date"],
            ("pos", "zone"): ["zone_cd", "zone_nm", "seat_style"],
            ("pos", "table"): ["table_no", "zone_cd", "seats"],
            ("pos", "menu_category"): ["category_cd", "category_nm"],
            ("pos", "menu"): ["menu_cd", "menu_nm", "sale_price", "category_cd", "kitchen_cd", "soldout_yn"],
            ("pos", "visit"): ["visit_no", "member_no", "arrive_dt", "seat_dt", "leave_dt", "party_cd", "headcount",
                               "kid_yn", "rsv_yn", "table_no"],
            ("pos", "waiting"): ["wait_no", "visit_no", "reg_dt", "call_dt", "quoted_min", "status"],
            ("pos", "order"): ["order_no", "visit_no", "order_dt"],
            ("pos", "order_line"): ["order_no", "menu_cd", "qty", "price"],
            ("pos", "payment"): ["pay_no", "visit_no", "pay_dt", "amount", "pay_type", "close_no"],
            ("pos", "daily_close"): ["close_no", "biz_date", "sales_total", "pay_count"],
            ("mes", "recipe"): ["recipe_code", "recipe_name", "station"],
            ("mes", "recipe_line"): ["recipe_code", "material_name", "qty"],
            ("mes", "kds_ticket"): ["ticket_id", "pos_order_no", "recipe_code", "station", "qty", "start_ts", "done_ts"],
            ("mes", "consumption"): ["cons_id", "ticket_id", "material_name", "qty", "kind", "ts", "close_no"],
            ("mes", "daily_consumption"): ["close_no", "biz_date"],
            ("scm", "item_master"): ["item_code", "item_name", "uom", "storage", "shelf_life_days", "safety_stock",
                                     "main_vendor"],
            ("scm", "vendor"): ["vendor_code", "vendor_name", "category", "lead_days", "pay_terms_days"],
            ("scm", "vendor_price"): ["vendor_code", "item_code", "unit_price", "moq"],
            ("scm", "purchase_order"): ["po_no", "vendor_code", "order_dt", "expected_date", "status"],
            ("scm", "po_line"): ["po_no", "item_code", "qty", "unit_price"],
            ("scm", "receipt"): ["receipt_no", "po_no", "item_code", "qty", "unit_cost", "received_dt"],
            ("scm", "stock_adjust"): ["adj_no", "item_code", "qty", "adj_dt", "reason"],
            ("erp", "hr_employee"): ["emp_no", "emp_name", "position", "emp_type", "monthly_pay", "hourly_pay"],
            ("erp", "shift_master"): ["shift_code", "shift_name", "blocks"],
            ("erp", "attendance"): ["att_no", "emp_no", "work_date", "shift_code", "clock_in", "clock_out"],
            ("erp", "chart_of_accounts"): ["acct_code", "acct_name", "acct_type"],
            ("erp", "journal"): ["je_no", "posting_date", "description", "source_doc_no", "source_type"],
            ("erp", "journal_line"): ["je_no", "acct_code", "debit", "credit"],
            ("erp", "payroll"): ["period_no", "period_start", "period_end"],
        }
        for (system, table), header in headers.items():
            write_csv(OUT / system / f"{table}.csv", header, R[system, table])
        return {f"{s}/{t}": len(R[s, t]) for s, t in headers}

    def run(self):
        d = self.start
        while d <= self.end:
            if WEEKDAY_KO[d.weekday()] != PROFILE["restaurant"]["closed_weekday"]:
                self.simulate_day(d)
            d += timedelta(days=1)
        self.run_scm()
        self.run_erp()
        return self.write()


if __name__ == "__main__":
    counts = Store().run()
    for k, v in counts.items():
        print(f"{k:28s} {v:>8,}")
