# Index Performance Compare

SQL Server 환경에서 힙(Heap), 비클러스터형 인덱스(Non-Clustered Index), 클러스터형 인덱스(Clustered Index) 구조 간의 CRUD 성능을 벤치마킹하고 시각화하는 프로젝트입니다.

## 🎯 벤치마크 목적 (Purpose)

본 프로젝트는 데이터베이스 인덱스에 대해 인터넷 상에 널리 알려진 다음 두 가지 통념을 실제 데이터를 통해 수치적으로 검증하고자 기획되었습니다:
1. **"Non-Clustered Index의 CUD(생성, 수정, 삭제) 연산 속도가 Clustered Index보다 더 빠르다"**라는 주장에 대한 실제 수치적 확인.
2. **"데이터 변경 빈도가 높은 환경에서는 인덱스를 적용하면 안 된다"**는 통설이 실제 벤치마크 환경에서도 유효하게 나타나는지 팩트체크.

## 📊 벤치마크 방법론 (Methodology)

본 벤치마크는 Python의 `pyodbc` 라이브러리를 사용하여 SQL Server에 접속하고, 100건부터 최대 100,000건의 데이터를 대상으로 테이블 구조별 쿼리 소요 시간을 측정합니다. 네트워크 오버헤드를 최소화하기 위해 모든 DML(Insert, Update, Delete) 쿼리는 `cursor.executemany`와 `fast_executemany=True` 옵션을 통해 일괄(Batch) 처리되었으며, 수동 Commit을 적용하여 트랜잭션 오버헤드를 최소화하고 순수 연산 속도를 측정했습니다.

### 각 연산별 측정 방식
1. **Insert (생성)**
   - **Sequential Insert**: 1부터 N까지 순차적으로 증가하는 ID(기본키 혹은 힙의 열)를 가진 데이터를 삽입하여 순차적인 페이지 채움 성능을 확인합니다.
   - **Random Insert**: 삽입 전 ID 배열을 무작위로 섞어(Shuffle) 비순차적 삽입 성능(페이지 분할, 인덱스 재정렬 등)을 유도하고 측정합니다.
2. **Select Point (단일 조회)**
   - 전체 데이터 중 최대 100개의 ID를 무작위로 추출하여 단일 레코드를 조회(`SELECT * WHERE id = ?`)합니다. 측정된 총 실행 시간을 샘플링 횟수(100)로 나누어 **1회 조회당 평균 수행 시간**을 벤치마킹합니다.
3. **Select Range (범위 조회)**
   - 전체 데이터의 10%에 해당하는 연속된 범위(`WHERE id BETWEEN ? AND ?`)를 100회 무작위로 지정하여 반복 조회합니다. 측정된 총 실행 시간을 샘플링 횟수(100)로 나누어 **1회 범위 조회당 평균 수행 시간**을 구합니다.
4. **Update (수정)**
   - 최대 100개의 무작위 ID를 추출해 해당 레코드의 값을 일괄 변경(`UPDATE ... WHERE id = ?`)하고 소요 시간을 측정합니다.
5. **Delete (삭제)**
   - 최대 100개의 무작위 ID를 추출해 해당 레코드를 일괄 삭제(`DELETE FROM ... WHERE id = ?`)하고 소요 시간을 측정합니다.

---

## 📈 실험 결과 요약 (Results)

(100,000건 데이터 기준 벤치마크 결과)
- **Select Point (단일 조회)**: Clustered Index(0.0011s)와 Heap + NC Index(0.0011s)가 Pure Heap(0.0068s) 대비 더 빠른 수행 시간을 기록했습니다.
- **Select Range (범위 조회)**: Clustered Index(0.0091s)가 가장 빠른 수행 시간을 보였으며, Pure Heap(0.0297s)과 Heap + NC Index(0.0458s) 순으로 측정되었습니다.
- **Update & Delete (수정 및 삭제)**: Clustered Index와 Heap + NC Index 모두 약 0.01s~0.02s의 소요 시간을 기록하여, 약 1.7s가 소요된 Pure Heap보다 빠른 성능을 보였습니다.
- **Insert (삽입)**: Sequential 및 Random Insert 모두 Pure Heap과 Clustered Index의 삽입 속도(약 4.6s~5.0s)가 유사하게 측정되었으며, Heap + NC Index(약 5.7s)가 상대적으로 높은 수행 시간을 기록했습니다.

---

## 💻 시스템 환경 (System Specifications)
벤치마크 실험이 진행된 하드웨어 및 소프트웨어 환경은 다음과 같습니다.
- **OS**: Ubuntu 24.04 LTS
- **CPU**: Intel(R) Core(TM) i5-12400F (6 Cores / 12 Threads)
- **RAM**: 32 GB
- **Storage**: 120GB SSD / 1TB Samsung NVMe SSD
- **Database**: Microsoft SQL Server 2022 (Linux)

---

## 🛠 실행 방법 (How to Run)

### 1. 요구 사항 (Prerequisites)
- **Python 3.7+**
- **ODBC Driver 18 for SQL Server** (또는 운영체제에 맞는 SQL Server ODBC 드라이버)
- 실행에 필요한 Python 패키지 설치:
  ```bash
  pip install pyodbc matplotlib
  ```

### 2. 설정 파일 구성 (Configuration)
프로젝트 루트 디렉토리에 `db_config.example.json` 파일을 복사하여 `db_config.json`을 생성하고 데이터베이스 자격 증명을 입력합니다.
```json
{
    "DRIVER": "{ODBC Driver 18 for SQL Server}",
    "SERVER": "localhost",
    "UID": "your_username",
    "PWD": "your_password",
    "DATABASE": "database_name"
}
```

### 3. 벤치마크 실행 (Execution)
터미널에서 아래의 명령어를 실행하여 벤치마크를 수행합니다.
```bash
# 전체 세부 벤치마크 실행 (Sequential/Random Insert 및 디테일한 CRUD 측정)
python3 src/benchmark.py
```
실행이 완료되면 루트 디렉토리 아래 `result/` 폴더에 벤치마크 결과 데이터를 담은 `.json` 파일과 시각화된 통계 차트(`.png`)들이 자동으로 저장됩니다.

---

## 📝 License
This project is licensed under the MIT License.
