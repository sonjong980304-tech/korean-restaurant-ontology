"""Neo4j 접속 공용 모듈. 접속 정보는 .env에서만 읽는다."""
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


def get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        notifications_min_severity="OFF",   # '아직 없는 속성' 같은 안내 경고로 로그가 넘치지 않게
    )


if __name__ == "__main__":
    with get_driver() as driver:
        driver.verify_connectivity()
        rec = driver.execute_query("CALL dbms.components() YIELD versions RETURN versions[0] AS v").records[0]
        apoc = driver.execute_query("RETURN apoc.version() AS v").records[0]
        print(f"Neo4j {rec['v']} 접속 성공 / APOC {apoc['v']}")
