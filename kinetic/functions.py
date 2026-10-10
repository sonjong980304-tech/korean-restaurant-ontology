"""Kinetic 레이어 — Function(읽기 전용 계산).

온톨로지(Neo4j)를 읽어 지표를 계산할 뿐 데이터를 바꾸지 않는다. 데이터를 바꾸는 것은 actions.py(5단계).
모든 함수는 driver를 받고, 기간·기준일은 date로 받는다.
"""
from datetime import date, datetime, time, timedelta


DATA_START, DATA_END = date(2026, 7, 1), date(2026, 9, 30)   # 원천 데이터 기간(예측·비교의 경계)


def _day_end(d: date) -> datetime:
    """d일 하루가 끝나는 시각(다음날 0시) — '기준일 포함'을 '< 다음날 0시'로 표현한다."""
    return datetime.combine(d + timedelta(days=1), time())


def _native(row):
    """Neo4j 날짜·시각 타입을 파이썬 date/datetime으로 바꾼다."""
    return {k: (v.to_native() if hasattr(v, "to_native") else v) for k, v in row.items()}


def _period(start: date, end: date):
    return {"start": datetime.combine(start, time()), "until": _day_end(end)}


SLOT = "CASE WHEN v.arrived_at.hour < 16 THEN '점심' ELSE '저녁' END"


LATEST_PRICE = """
    CALL (i) {
      OPTIONAL MATCH (po:PurchaseOrder)-[x:ORDERS_ITEM]->(i)
      WHERE po.ordered_at < $until AND NOT po.status IN ['요청', '취소']   // 아직 사지 않은 발주는 단가 근거가 아님
      RETURN x.unit_price AS unit_price ORDER BY po.ordered_at DESC LIMIT 1
    }"""


def latest_prices(driver, as_of: date):
    """재료별 기준일까지 가장 최근 매입단가(원/기준단위)."""
    records = driver.execute_query(
        "MATCH (i:Ingredient)" + LATEST_PRICE + " RETURN i.ingredient_id AS iid, unit_price",
        until=_day_end(as_of)).records
    return {r["iid"]: r["unit_price"] for r in records if r["unit_price"] is not None}


def menu_costs(driver, as_of: date):
    """메뉴별 원가(레시피 사용량 × 기준일까지의 최근 매입단가), 마진, 원가율."""
    records = driver.execute_query(
        """
        MATCH (m:MenuItem)-[u:USES]->(i:Ingredient)""" + LATEST_PRICE + """
        WITH m, sum(u.qty_per_serving * unit_price) AS cost, count(*) - count(unit_price) AS unpriced
        RETURN m.menu_id AS menu_id, m.name AS menu, m.price AS price, cost,
               m.price - cost AS margin, cost / m.price AS cost_ratio, unpriced
        ORDER BY menu_id
        """,
        until=_day_end(as_of),
    ).records
    return [dict(r) for r in records]


def menu_engineering(driver, start: date, end: date, exclude_categories=("주류·음료",)):
    """Kasavana–Smith 메뉴 엔지니어링.

    인기: 판매비중 ≥ 0.7 × (1 / 메뉴 수)   마진: 공헌이익 ≥ 판매수량 가중평균 공헌이익
    Star(인기·고마진) / Plowhorse(인기·저마진) / Puzzle(비인기·고마진) / Dog(비인기·저마진)
    """
    sold = {
        r["menu_id"]: r["sold"]
        for r in driver.execute_query(
            """
            MATCH (m:MenuItem)-[:IN_CATEGORY]->(c:MenuCategory) WHERE NOT c.name IN $exclude
            OPTIONAL MATCH (o:Order)-[x:CONTAINS]->(m) WHERE $start <= o.ordered_at < $until
            RETURN m.menu_id AS menu_id, coalesce(sum(x.qty), 0) AS sold
            """,
            exclude=list(exclude_categories), **_period(start, end),
        ).records
    }
    costs = {r["menu_id"]: r for r in menu_costs(driver, as_of=end) if r["menu_id"] in sold}
    total = sum(sold.values())
    if not total:
        return []
    popularity_cut = 0.7 / len(sold)
    margin_cut = sum(sold[m] * costs[m]["margin"] for m in sold) / total
    rows = []
    for menu_id, n in sold.items():
        c = costs[menu_id]
        popular, profitable = n / total >= popularity_cut, c["margin"] >= margin_cut
        quadrant = {(True, True): "Star", (True, False): "Plowhorse",
                    (False, True): "Puzzle", (False, False): "Dog"}[(popular, profitable)]
        rows.append({"menu_id": menu_id, "menu": c["menu"], "sold": n, "mix": n / total, "margin": c["margin"],
                     "popularity_threshold": popularity_cut, "avg_margin_threshold": margin_cut,
                     "quadrant": quadrant})
    return sorted(rows, key=lambda r: -r["sold"])



def table_turnover(driver, start: date, end: date):
    """날짜·시간대(점심/저녁)별 테이블 회전율 = 착석 팀 수 ÷ 테이블 수, 좌석 회전율 = 착석 인원 ÷ 좌석 수."""
    records = driver.execute_query(
        f"""
        MATCH (t:DiningTable) WITH count(t) AS tables, sum(t.capacity) AS seats
        MATCH (v:Visit) WHERE v.seated_at IS NOT NULL AND $start <= v.arrived_at < $until
        WITH tables, seats, date(v.arrived_at) AS date, {SLOT} AS slot, v
        WITH tables, seats, date, slot, count(v) AS parties, sum(v.party_size) AS guests
        RETURN date, slot, parties, guests,
               toFloat(parties) / tables AS table_turns, toFloat(guests) / seats AS seat_turns
        ORDER BY date, slot
        """,
        **_period(start, end),
    ).records
    return [_native(r) for r in records]


def waiting_stats(driver, start: date, end: date):
    """동행유형별 방문 수, 대기 이탈률, 착석 팀의 평균 대기시간(분)."""
    records = driver.execute_query(
        """
        MATCH (v:Visit) WHERE $start <= v.arrived_at < $until
        WITH v.party_type AS party_type, v.seated_at IS NULL AS abandoned,
             CASE WHEN v.seated_at IS NULL THEN null
                  ELSE duration.inSeconds(v.arrived_at, v.seated_at).seconds / 60.0 END AS wait
        RETURN party_type, count(*) AS visits, sum(CASE WHEN abandoned THEN 1 ELSE 0 END) AS abandoned,
               toFloat(sum(CASE WHEN abandoned THEN 1 ELSE 0 END)) / count(*) AS abandon_rate,
               avg(wait) AS avg_wait_min
        ORDER BY abandon_rate DESC
        """,
        **_period(start, end),
    ).records
    return [dict(r) for r in records]


def monthly_cost_ratio(driver):
    """월별 매출(수익 계정 대변) 대비 식재료비(식재료비 계정 차변) 비율 — ERP 전표 기준."""
    records = driver.execute_query(
        """
        MATCH (je:JournalEntry)
        WITH je, substring(toString(je.posted_on), 0, 7) AS month
        OPTIONAL MATCH (je)-[s:CREDITS]->(:Account {account_type: '수익'})
        OPTIONAL MATCH (je)-[c:DEBITS]->(:Account {name: '식재료비'})
        WITH month, sum(coalesce(s.amount, 0)) AS sales, sum(coalesce(c.amount, 0)) AS food_cost
        WHERE sales > 0
        RETURN month, sales, food_cost, toFloat(food_cost) / sales AS cost_ratio
        ORDER BY month
        """
    ).records
    return [dict(r) for r in records]


def usage_variance(driver, start: date, end: date):
    """재료별 레시피상 사용량(조리 수량 × 1인분 레시피) 대비 실제 소진량, 그리고 폐기율.

    actual은 조리지시에 연결된 소진만 센다(레시피와 비교하려고). 폐기율의 분모는 반찬처럼 조리지시 없는
    소진까지 포함한 전체 사용량(total_usage = 소진 + 폐기)이다.
    """
    records = driver.execute_query(
        """
        MATCH (k:CookTicket)-[:COOKS]->(:MenuItem)-[u:USES]->(i:Ingredient)
        WHERE $start <= k.started_at < $until
        OPTIONAL MATCH (k)<-[:CONSUMED_BY]-(s:StockMovement)-[:MOVES]->(i)
        WITH i, k, u, -sum(coalesce(s.qty, 0)) AS used
        WITH i, sum(k.qty * u.qty_per_serving) AS theoretical, sum(used) AS actual
        OPTIONAL MATCH (w:StockMovement)-[:MOVES]->(i)
        WHERE w.movement_type IN ['소진', '폐기'] AND $start <= w.moved_at < $until
        WITH i, theoretical, actual, -sum(coalesce(w.qty, 0)) AS total_usage,
             -sum(CASE WHEN w.movement_type = '폐기' THEN w.qty ELSE 0 END) AS waste
        RETURN i.name AS ingredient, i.unit AS unit, theoretical, actual,
               (actual - theoretical) / theoretical AS variance_rate,
               total_usage, waste, CASE WHEN total_usage > 0 THEN waste / total_usage END AS waste_rate
        ORDER BY variance_rate DESC
        """,
        **_period(start, end),
    ).records
    return [dict(r) for r in records]


def stock_status(driver, as_of: date, window_days: int = 7):
    """기준일 현재고(모든 재고이동 합계), 최근 window_days일 하루 평균 사용량(소진+폐기), 소진 예상일, 안전재고 미달 여부."""
    records = driver.execute_query(
        """
        MATCH (i:Ingredient)
        OPTIONAL MATCH (s:StockMovement)-[:MOVES]->(i) WHERE s.moved_at < $until
        WITH i, sum(coalesce(s.qty, 0)) AS stock
        OPTIONAL MATCH (u:StockMovement)-[:MOVES]->(i)
        WHERE u.movement_type IN ['소진', '폐기'] AND $window_start <= u.moved_at < $until
        WITH i, stock, -sum(coalesce(u.qty, 0)) / $days AS daily_usage
        RETURN i.ingredient_id AS ingredient_id, i.name AS ingredient, i.unit AS unit, stock, daily_usage,
               i.safety_stock AS safety_stock,
               CASE WHEN daily_usage > 0 THEN stock / daily_usage END AS days_left,
               stock < i.safety_stock AS below_safety
        ORDER BY days_left
        """,
        until=_day_end(as_of), window_start=datetime.combine(as_of - timedelta(days=window_days - 1), time()),
        days=float(window_days),
    ).records
    return [dict(r) for r in records]


REGULAR_VISITS, REGULAR_WINDOW_DAYS, CHURN_DAYS, VIP_PERCENTILE = 4, 90, 30, 0.8


def _percentile(values, q):
    """선형 보간 백분위수(Neo4j percentileCont와 같은 방식)."""
    xs = sorted(values)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def customer_segments(driver, as_of: date):
    """회원별 세그먼트. 착석한 방문만 센다(대기 이탈은 제외).

    이탈위험: 마지막 방문 시점엔 단골이었는데 CHURN_DAYS일 이상 미방문
    단골:     최근 REGULAR_WINDOW_DAYS일 안에 REGULAR_VISITS회 이상 + 마지막 방문이 CHURN_DAYS일 이내
    VIP:      단골 중 1회 평균 결제액이 단골 상위 20%(VIP_PERCENTILE 이상)
    재방문:   누적 2회 이상 / 신규: 누적 1회
    """
    records = driver.execute_query(
        """
        MATCH (c:Customer)-[:MADE_VISIT]->(v:Visit)
        WHERE v.seated_at IS NOT NULL AND v.arrived_at < $until
        OPTIONAL MATCH (v)-[:PAID_BY]->(p:Payment)
        WITH c, v, sum(coalesce(p.amount, 0)) AS spend
        RETURN c.customer_id AS customer_id, collect([date(v.arrived_at), spend]) AS visits
        """,
        until=_day_end(as_of),
    ).records

    def count_within(days, ref):
        return sum(1 for d in days if ref - timedelta(days=REGULAR_WINDOW_DAYS) < d <= ref)

    rows = []
    for r in records:
        days = sorted(v[0].to_native() for v in r["visits"])
        spends = [v[1] for v in r["visits"]]
        last = days[-1]
        since = (as_of - last).days
        if count_within(days, as_of) >= REGULAR_VISITS and since < CHURN_DAYS:
            segment = "단골"
        elif count_within(days, last) >= REGULAR_VISITS and since >= CHURN_DAYS:
            segment = "이탈위험"
        elif len(days) >= 2:
            segment = "재방문"
        else:
            segment = "신규"
        rows.append({"customer_id": r["customer_id"], "segment": segment, "visits": len(days),
                     "visits_90d": count_within(days, as_of), "last_visit": last, "days_since_last": since,
                     "avg_spend": sum(spends) / len(spends)})

    regulars = [r for r in rows if r["segment"] == "단골"]
    if regulars:
        cut = _percentile([r["avg_spend"] for r in regulars], VIP_PERCENTILE)
        for r in regulars:
            if r["avg_spend"] >= cut:
                r["segment"] = "VIP"
    return sorted(rows, key=lambda r: r["customer_id"])


def segment_summary(driver, as_of: date):
    """세그먼트별 회원 수."""
    counts = {s: 0 for s in ("신규", "재방문", "단골", "VIP", "이탈위험")}
    for r in customer_segments(driver, as_of):
        counts[r["segment"]] += 1
    return counts
