// ① 양파값이 오르면 어떤 메뉴가 타격받나? (MES 레시피 → SCM 재료 → POS 매출)
MATCH (i:Ingredient {name:'양파'})<-[u:USES]-(m:MenuItem)<-[c:CONTAINS]-(:Order)
RETURN m.name AS 메뉴, u.qty_per_serving AS 양파_g, sum(c.qty) AS 판매수량, sum(c.qty*c.unit_price) AS 매출
ORDER BY 매출 DESC LIMIT 5;

// ② 전표 하나를 원천 거래까지 거슬러 올라가기 (ERP → POS → 방문 → 고객)
MATCH (je:JournalEntry {je_id:'JE-S20260904'})-[:POSTS_SALES]->(p:Payment)<-[:PAID_BY]-(v:Visit)
OPTIONAL MATCH (c:Customer)-[:MADE_VISIT]->(v)
RETURN je.je_id AS 전표, count(p) AS 결제건수, sum(p.amount) AS 결제합계, count(c) AS 회원방문;

// ③ 직원 근태(ERP)와 조리시간(MES): 요일·점심/저녁별 평균 조리시간
MATCH (t:CookTicket) WHERE t.station IN ['찌개','구이']
WITH t, t.started_at.hour < 16 AS 점심, ['월','화','수','목','금','토','일'][t.started_at.dayOfWeek-1] AS 요일
RETURN 요일, 점심, round(avg(duration.between(t.started_at, t.completed_at).seconds)/60.0, 1) AS 평균조리분
ORDER BY 평균조리분 DESC LIMIT 4;

// ④ 동행유형별 대기 이탈률과 체류시간
MATCH (v:Visit)
RETURN v.party_type AS 동행, count(*) AS 방문,
       round(100.0*sum(CASE WHEN v.seated_at IS NULL THEN 1 ELSE 0 END)/count(*),1) AS 이탈률,
       round(avg(duration.between(v.seated_at, v.left_at).minutes),0) AS 체류분
ORDER BY 이탈률 DESC;

// ⑤ 선의 근거(계보) 보기 — 시스템을 넘나드는 선이 어떤 규칙과 키값으로 만들어졌나
MATCH (o:Order)-[t:TRIGGERED]->(k:CookTicket)-[c:COOKS]->(m:MenuItem)-[u:USES]->(i:Ingredient)
RETURN o.order_id AS 주문, k.ticket_id AS 조리지시, m.name AS 메뉴, i.name AS 재료,
       t._join_key AS 주문연결키, t._rule AS 주문연결규칙, c._join_key AS 레시피코드, u._join_key AS 재료매핑
LIMIT 5;
